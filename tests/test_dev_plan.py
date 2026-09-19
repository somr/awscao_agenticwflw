from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".agentic-sdlc" / "cao" / "workflows" / "dev_plan.py"

stub = types.ModuleType("cao_workflow")
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault("cao_workflow", stub)

# Exercise the exact standalone artifact installed into CAO, not a second
# compatibility implementation of the extracted helpers.
sys.path.insert(0, str(ROOT / ".agentic-sdlc" / "cao"))
from build_workflow import build_source
if os.environ.get("SDLC_TEST_SOURCE") == "1":
    mod = importlib.import_module("sdlc_workflows.planning")
else:
    mod = types.ModuleType("dev_plan_bundle")
    exec(compile(build_source("dev_plan"), "<bundled:dev_plan>", "exec"), mod.__dict__)


def valid_context() -> dict:
    jira_ref = [{"source_id": "JIRA:T-1", "location": "AC-1"}]
    return {
        "schema_version": "1.0",
        "ticket": {"id": "T-1", "summary": "Demo"},
        "sources": [
            {"source_id": "JIRA:T-1", "type": "JIRA", "title": "Demo", "status": "RETRIEVED", "content_digest": None}
        ],
        "problem_statement": {"text": "Do a thing", "sources": jira_ref},
        "scope": {"in": [{"text": "Thing", "sources": jira_ref}], "out": [], "uncertain": []},
        "acceptance_criteria": [
            {"id": "AC-1", "text": "Thing works", "origin": "EXPLICIT", "sources": jira_ref}
        ],
        "functional_requirements": [],
        "non_functional_requirements": [],
        "constraints": [],
        "dependencies": [],
        "open_questions": [],
        "contradictions": [],
        "retrieval_warnings": [],
    }


class WorkflowHelpersTest(unittest.TestCase):
    def test_valid_context_is_ready(self):
        self.assertEqual(mod.validate_planning_context(valid_context(), "T-1"), [])

    def test_blocking_question_stops_readiness(self):
        context = valid_context()
        context["open_questions"].append({
            "id": "Q-1",
            "question": "Which behaviour?",
            "reason": "Not specified",
            "blocking": True,
            "sources": [{"source_id": "JIRA:T-1", "location": "description"}],
        })
        blockers = mod.validate_planning_context(context, "T-1")
        self.assertIn("blocking open question Q-1", blockers)

    def test_unknown_provenance_is_rejected(self):
        context = valid_context()
        context["acceptance_criteria"][0]["sources"][0]["source_id"] = "CONF:UNKNOWN"
        with self.assertRaises(mod.WorkflowContractError):
            mod.validate_planning_context(context, "T-1")

    def test_review_status_must_match_findings(self):
        review = {
            "review_status": "PASS",
            "summary": "looks fine",
            "findings": [{
                "id": "PLAN-1",
                "impact": "MEDIUM",
                "category": "TESTING",
                "disposition": "PLAN_CHANGE_REQUIRED",
                "plan_section": "Verification",
                "description": "Missing test",
                "evidence": ["tests/example.py"],
                "required_action": "Add verification",
                "confidence": 0.9,
            }],
        }
        with self.assertRaises(mod.WorkflowContractError):
            mod.validate_review(review)

    def test_fixture_retrieval_marks_missing_required_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input"
            raw = base / "raw"
            source.mkdir()
            (source / "jira.md").write_text("ticket", encoding="utf-8")
            (source / "context.json").write_text(json.dumps({
                "schema_version": "1.0",
                "ticket": {
                    "id": "T-1", "source_id": "JIRA:T-1", "title": "T", "file": "jira.md", "required": True
                },
                "confluence": [{
                    "source_id": "CONF:1", "title": "Missing", "file": "missing.md", "required": True
                }],
            }), encoding="utf-8")
            retrieval = mod.retrieve_fixture_sources(source, raw, "T-1")
            self.assertEqual(mod.retrieval_blockers(retrieval), ["required source unavailable: CONF:1"])



# The same lifecycle assertions run against the shared module and the installed bundle.
runtime_mod = importlib.import_module("sdlc_workflows.runtime") if os.environ.get("SDLC_TEST_SOURCE") == "1" else mod


class CommonRuntimeTest(unittest.TestCase):
    def test_json_fence_tolerance(self):
        self.assertEqual(runtime_mod._parse_json_output('```json\n{"a": 1}\n```', "x"), {"a": 1})


    def test_run_delivered_step_uses_answer_suffix_and_appends_instructions(self):
        # Every direct main() call site (planning analyst, plan author) goes
        # through this helper with answer_suffix=".answer.md" for Markdown
        # steps, distinct from the JSON-contract default ".answer.json".
        seen = {}

        def fake_run(**kwargs):
            seen.update(kwargs)
            return "the answer"

        original_run = runtime_mod._run_stabilized_step
        runtime_mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                result = runtime_mod._run_delivered_step(
                    agent="test-agent",
                    prompt="Write the analysis",
                    step_id="planning-analysis-v1",
                    repo=Path(temp),
                    evidence_dir=evidence_dir,
                    answer_suffix=".answer.md",
                )
                self.assertEqual(result, "the answer")
                self.assertEqual(seen["answer_path"], evidence_dir / "planning-analysis-v1.answer.md")
                self.assertIn(str(seen["answer_path"]), seen["prompt"])
                self.assertIn("OUTPUT DELIVERY", seen["prompt"])
        finally:
            runtime_mod._run_stabilized_step = original_run

    def test_run_stabilized_step_keeps_terminal_alive_until_wrapper_cleanup(self):
        calls = {}

        def fake_step(*args, **kwargs):
            calls["step_kwargs"] = kwargs
            return types.SimpleNamespace(
                output="initial", terminal_id="term-1", replayed=False
            )

        def fake_wait_for_answer_file(**kwargs):
            calls["wait_for_answer_file"] = kwargs
            return "final answer"

        def fake_cleanup(*args, **kwargs):
            calls["cleanup"] = (args, kwargs)

        original_step = runtime_mod.step
        original_wait_for_answer_file = runtime_mod._wait_for_answer_file
        original_cleanup = runtime_mod._cleanup_step_terminal
        runtime_mod.step = fake_step
        runtime_mod._wait_for_answer_file = fake_wait_for_answer_file
        runtime_mod._cleanup_step_terminal = fake_cleanup
        try:
            with tempfile.TemporaryDirectory() as temp:
                answer_path = Path(temp) / "evidence" / "stable-step.answer.json"
                result = runtime_mod._run_stabilized_step(
                    agent="test-agent",
                    prompt="do it",
                    step_id="stable-step",
                    repo=Path(temp),
                    evidence_dir=Path(temp) / "evidence",
                    answer_path=answer_path,
                )
            self.assertEqual(result, "final answer")
            self.assertFalse(calls["step_kwargs"]["teardown"])
            self.assertEqual(calls["wait_for_answer_file"]["terminal_id"], "term-1")
            self.assertEqual(calls["wait_for_answer_file"]["answer_path"], answer_path)
            self.assertIn("cleanup", calls)
        finally:
            runtime_mod.step = original_step
            runtime_mod._wait_for_answer_file = original_wait_for_answer_file
            runtime_mod._cleanup_step_terminal = original_cleanup

    def test_run_stabilized_step_replay_reads_answer_file_from_disk(self):
        def fake_step(*args, **kwargs):
            return types.SimpleNamespace(output="stale terminal text", terminal_id="dead", replayed=True)

        original_step = runtime_mod.step
        runtime_mod.step = fake_step
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                evidence_dir.mkdir()
                answer_path = evidence_dir / "s1.answer.json"
                answer_path.write_text('{"ok": true}', encoding="utf-8")
                result = runtime_mod._run_stabilized_step(
                    agent="test-agent",
                    prompt="do it",
                    step_id="s1",
                    repo=Path(temp),
                    evidence_dir=evidence_dir,
                    answer_path=answer_path,
                )
                self.assertEqual(result, '{"ok": true}')
        finally:
            runtime_mod.step = original_step

    def test_run_stabilized_step_replay_without_answer_file_raises(self):
        def fake_step(*args, **kwargs):
            return types.SimpleNamespace(output="stale terminal text", terminal_id="dead", replayed=True)

        original_step = runtime_mod.step
        runtime_mod.step = fake_step
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                with self.assertRaises(runtime_mod.IncompleteAgentExecutionError):
                    runtime_mod._run_stabilized_step(
                        agent="test-agent",
                        prompt="do it",
                        step_id="s1",
                        repo=Path(temp),
                        evidence_dir=evidence_dir,
                        answer_path=evidence_dir / "s1.answer.json",
                    )
        finally:
            runtime_mod.step = original_step

    def test_answer_file_delivery_instructions_embed_path_and_forbid_chat_json(self):
        path = Path("/tmp/evidence/s1.answer.json")
        text = runtime_mod._answer_file_delivery_instructions(path)
        self.assertIn(str(path), text)
        self.assertIn("Do not print the answer", text)

    def test_wait_for_answer_file_returns_content_once_stable(self):
        original_status = runtime_mod._cao_terminal_status
        original_wait = runtime_mod._wait
        runtime_mod._cao_terminal_status = lambda terminal_id: "completed"
        runtime_mod._wait = lambda seconds: None
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                evidence_dir.mkdir()
                answer_path = evidence_dir / "s1.answer.json"
                answer_path.write_text('{"ok": true}', encoding="utf-8")

                result = runtime_mod._wait_for_answer_file(
                    terminal_id="term-1",
                    answer_path=answer_path,
                    step_id="s1",
                    evidence_dir=evidence_dir,
                )
                self.assertEqual(result, '{"ok": true}')
                evidence = json.loads((evidence_dir / "s1.stabilization.json").read_text())
                self.assertTrue(evidence["stabilized"])
                self.assertEqual(len(evidence["polls"]), 2)
        finally:
            runtime_mod._cao_terminal_status = original_status
            runtime_mod._wait = original_wait

    def test_wait_for_answer_file_raises_on_terminal_error_status(self):
        original_status = runtime_mod._cao_terminal_status
        original_wait = runtime_mod._wait
        runtime_mod._cao_terminal_status = lambda terminal_id: "error"
        runtime_mod._wait = lambda seconds: None
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                with self.assertRaises(runtime_mod.IncompleteAgentExecutionError):
                    runtime_mod._wait_for_answer_file(
                        terminal_id="term-1",
                        answer_path=evidence_dir / "s1.answer.json",
                        step_id="s1",
                        evidence_dir=evidence_dir,
                    )
        finally:
            runtime_mod._cao_terminal_status = original_status
            runtime_mod._wait = original_wait

    def test_wait_for_answer_file_times_out_if_never_written(self):
        original_status = runtime_mod._cao_terminal_status
        original_wait = runtime_mod._wait
        original_max_polls = runtime_mod.COMPLETION_MAX_POLLS
        runtime_mod._cao_terminal_status = lambda terminal_id: "completed"
        runtime_mod._wait = lambda seconds: None
        runtime_mod.COMPLETION_MAX_POLLS = 2
        try:
            with tempfile.TemporaryDirectory() as temp:
                evidence_dir = Path(temp) / "evidence"
                with self.assertRaises(runtime_mod.IncompleteAgentExecutionError):
                    runtime_mod._wait_for_answer_file(
                        terminal_id="term-1",
                        answer_path=evidence_dir / "s1.answer.json",
                        step_id="s1",
                        evidence_dir=evidence_dir,
                    )
                evidence = json.loads((evidence_dir / "s1.stabilization.json").read_text())
                self.assertFalse(evidence["stabilized"])
        finally:
            runtime_mod._cao_terminal_status = original_status
            runtime_mod._wait = original_wait
            runtime_mod.COMPLETION_MAX_POLLS = original_max_polls

    def test_json_contract_step_uses_per_attempt_answer_path_and_delivery_instructions(self):
        seen = []
        outputs = iter(['{foo: "bar"}', '{"foo": "bar"}'])

        def fake_run(**kwargs):
            seen.append(kwargs)
            return next(outputs)

        original_run = runtime_mod._run_stabilized_step
        runtime_mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                runtime_mod._run_json_contract_step(
                    agent="test-agent",
                    prompt="Return JSON",
                    label="test",
                    step_id="json-test",
                    repo=Path(temp),
                    evidence_dir=Path(temp) / "evidence",
                    validator=lambda x: x,
                )
            self.assertEqual(
                [call["answer_path"].name for call in seen],
                ["json-test.answer.json", "json-test-repair-1.answer.json"],
            )
            self.assertIn(str(seen[0]["answer_path"]), seen[0]["prompt"])
            self.assertIn("OUTPUT DELIVERY", seen[0]["prompt"])
        finally:
            runtime_mod._run_stabilized_step = original_run

    def test_json_contract_step_repairs_malformed_completed_response_once(self):
        calls = []
        outputs = iter([
            '{foo: "bar"}',
            '{"foo": "bar"}',
        ])

        def fake_run(**kwargs):
            calls.append(kwargs["step_id"])
            return next(outputs)

        original_run = runtime_mod._run_stabilized_step
        runtime_mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                value = runtime_mod._run_json_contract_step(
                    agent="test-agent",
                    prompt="Return JSON",
                    label="test",
                    step_id="json-test",
                    repo=Path(temp),
                    evidence_dir=Path(temp) / "evidence",
                    validator=lambda x: x,
                )
                self.assertEqual(value, {"foo": "bar"})
                self.assertEqual(calls, ["json-test", "json-test-repair-1"])
                self.assertTrue((Path(temp) / "evidence" / "json-test.raw.txt").is_file())
                self.assertTrue((Path(temp) / "evidence" / "json-test-repair-1.raw.txt").is_file())
        finally:
            runtime_mod._run_stabilized_step = original_run

    def test_json_contract_step_repairs_validator_failure(self):
        calls = []
        outputs = iter([
            '{"foo": "wrong"}',
            '{"foo": "right"}',
        ])

        def fake_run(**kwargs):
            calls.append(kwargs["step_id"])
            return next(outputs)

        def validator(value):
            if value.get("foo") != "right":
                raise runtime_mod.WorkflowContractError("foo must be right")
            return value

        original_run = runtime_mod._run_stabilized_step
        runtime_mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                value = runtime_mod._run_json_contract_step(
                    agent="test-agent",
                    prompt="Return JSON",
                    label="test",
                    step_id="shape-test",
                    repo=Path(temp),
                    evidence_dir=Path(temp) / "evidence",
                    validator=validator,
                )
                self.assertEqual(value, {"foo": "right"})
                self.assertEqual(calls, ["shape-test", "shape-test-repair-1"])
        finally:
            runtime_mod._run_stabilized_step = original_run

    def test_incomplete_execution_never_enters_json_repair(self):
        calls = []

        def fake_run(**kwargs):
            calls.append(kwargs["step_id"])
            raise runtime_mod.IncompleteAgentExecutionError("worker still processing")

        original_run = runtime_mod._run_stabilized_step
        runtime_mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(runtime_mod.IncompleteAgentExecutionError):
                    runtime_mod._run_json_contract_step(
                        agent="test-agent",
                        prompt="Return JSON",
                        label="test",
                        step_id="json-test",
                        repo=Path(temp),
                        evidence_dir=Path(temp) / "evidence",
                    )
                self.assertEqual(calls, ["json-test"])
        finally:
            runtime_mod._run_stabilized_step = original_run


class _FakeAtlassianServer:
    """Serves canned Jira-issue / Confluence-page JSON on fixed paths,
    standing in for a real Atlassian tenant for retrieve_live_sources()
    tests. There is no live Jira/Confluence instance in this environment,
    so this fake server (same technique as
    tests/test_restrict_write_scope.py's _FakeTerminalServer) is the only
    verification the live adapter gets short of real credentials — see
    dev_plan.py's "Live Jira/Confluence adapter" module comment.
    """

    def __init__(self, routes: dict[str, tuple[int, dict | None]]):
        self._routes = routes
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
                path = self.path.split("?", 1)[0]
                match = outer._routes.get(path)
                if match is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = match
                body = b"not json{{{" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> int:
        self._thread.start()
        return self._server.server_address[1]

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def _live_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "ticket": {"id": "T-1", "source_id": "JIRA:T-1", "title": "Demo ticket", "jira_key": "PAY-1234", "required": True},
        "confluence": [
            {"source_id": "CONF:1", "title": "Feature spec", "page_id": "111", "required": True},
        ],
    }


JIRA_ADF_DESCRIPTION = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Do the thing."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "And the other thing."}]},
    ],
}


class AdfToTextTest(unittest.TestCase):
    def test_flattens_paragraphs_with_blank_line_between(self):
        text = mod._adf_to_text(JIRA_ADF_DESCRIPTION)
        self.assertEqual(text.strip(), "Do the thing.\n\nAnd the other thing.")

    def test_non_dict_non_string_node_is_empty(self):
        self.assertEqual(mod._adf_to_text(None), "")
        self.assertEqual(mod._adf_to_text(42), "")


class ConfluenceStorageToTextTest(unittest.TestCase):
    def test_strips_tags_and_keeps_visible_text(self):
        html = "<p>Some <strong>spec</strong> text.</p><ul><li>One</li><li>Two</li></ul>"
        self.assertEqual(mod._confluence_storage_to_text(html), "Some spec text.OneTwo")


class RetrieveLiveSourcesTest(unittest.TestCase):
    def setUp(self):
        self._saved_env = {
            name: os.environ.get(name)
            for name in (
                mod.JIRA_BASE_URL_ENV, mod.JIRA_API_TOKEN_ENV,
                mod.CONFLUENCE_BASE_URL_ENV, mod.CONFLUENCE_API_TOKEN_ENV,
            )
        }

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _set_env(self, port: int) -> None:
        base = f"http://127.0.0.1:{port}"
        os.environ[mod.JIRA_BASE_URL_ENV] = base
        os.environ[mod.JIRA_API_TOKEN_ENV] = "fake-jira-token"
        os.environ[mod.CONFLUENCE_BASE_URL_ENV] = base
        os.environ[mod.CONFLUENCE_API_TOKEN_ENV] = "fake-confluence-token"

    def test_happy_path_retrieves_ticket_and_confluence_page(self):
        routes = {
            "/rest/api/3/issue/PAY-1234": (200, {
                "fields": {"summary": "Make callbacks idempotent", "description": JIRA_ADF_DESCRIPTION},
            }),
            "/wiki/rest/api/content/111": (200, {
                "title": "Feature spec", "body": {"storage": {"value": "<p>Idempotency rules.</p>"}},
            }),
        }
        with _FakeAtlassianServer(routes) as port:
            self._set_env(port)
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                source = base / "input"
                raw = base / "raw"
                source.mkdir()
                (source / "context.json").write_text(json.dumps(_live_manifest()), encoding="utf-8")

                retrieval = mod.retrieve_live_sources(source, raw, "T-1")

                self.assertEqual(retrieval["adapter"], "jira_confluence_live")
                self.assertEqual(mod.retrieval_blockers(retrieval), [])
                by_id = {s["source_id"]: s for s in retrieval["sources"]}
                self.assertEqual(by_id["JIRA:T-1"]["status"], "RETRIEVED")
                self.assertEqual(by_id["JIRA:T-1"]["type"], "JIRA")
                jira_text = Path(by_id["JIRA:T-1"]["path"]).read_text(encoding="utf-8")
                self.assertIn("Make callbacks idempotent", jira_text)
                self.assertIn("Do the thing.", jira_text)
                self.assertEqual(by_id["CONF:1"]["status"], "RETRIEVED")
                self.assertEqual(by_id["CONF:1"]["type"], "CONFLUENCE")
                confluence_text = Path(by_id["CONF:1"]["path"]).read_text(encoding="utf-8")
                self.assertIn("Idempotency rules.", confluence_text)

    def test_missing_env_var_is_a_contract_error(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input"
            source.mkdir()
            (source / "context.json").write_text(json.dumps(_live_manifest()), encoding="utf-8")
            with self.assertRaises(mod.WorkflowContractError):
                mod.retrieve_live_sources(source, base / "raw", "T-1")

    def test_http_error_marks_required_source_unavailable(self):
        routes = {
            "/rest/api/3/issue/PAY-1234": (404, {"errorMessages": ["not found"]}),
            "/wiki/rest/api/content/111": (200, {
                "title": "Feature spec", "body": {"storage": {"value": "<p>Idempotency rules.</p>"}},
            }),
        }
        with _FakeAtlassianServer(routes) as port:
            self._set_env(port)
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                source = base / "input"
                source.mkdir()
                (source / "context.json").write_text(json.dumps(_live_manifest()), encoding="utf-8")

                retrieval = mod.retrieve_live_sources(source, base / "raw", "T-1")

                self.assertEqual(
                    mod.retrieval_blockers(retrieval), ["required source unavailable: JIRA:T-1"]
                )

    def test_ticket_id_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input"
            source.mkdir()
            (source / "context.json").write_text(json.dumps(_live_manifest()), encoding="utf-8")
            with self.assertRaises(mod.WorkflowContractError):
                mod.retrieve_live_sources(source, base / "raw", "some-other-ticket")


class RetrieveSourcesDispatchTest(unittest.TestCase):
    def test_default_source_adapter_input_is_local_fixture(self):
        self.assertEqual(mod.INPUTS["source_adapter"]["default"], "local_fixture")

    def test_dispatches_to_local_fixture_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input"
            raw = base / "raw"
            source.mkdir()
            (source / "jira.md").write_text("ticket", encoding="utf-8")
            (source / "context.json").write_text(json.dumps({
                "schema_version": "1.0",
                "ticket": {"id": "T-1", "source_id": "JIRA:T-1", "title": "T", "file": "jira.md", "required": True},
                "confluence": [],
            }), encoding="utf-8")
            retrieval = mod.retrieve_sources("local_fixture", source, raw, "T-1")
            self.assertEqual(retrieval["adapter"], "local_fixture")

    def test_unknown_adapter_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            with self.assertRaises(mod.WorkflowContractError):
                mod.retrieve_sources("not_a_real_adapter", base / "input", base / "raw", "T-1")


if __name__ == "__main__":
    unittest.main()
