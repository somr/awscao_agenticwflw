from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".agentic-sdlc" / "cao" / "workflows" / "dev_plan.py"

stub = types.ModuleType("cao_workflow")
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault("cao_workflow", stub)

spec = importlib.util.spec_from_file_location("dev_plan", WORKFLOW)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


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

    def test_json_fence_tolerance(self):
        self.assertEqual(mod._parse_json_output('```json\n{"a": 1}\n```', "x"), {"a": 1})


    def test_run_stabilized_step_keeps_terminal_alive_until_wrapper_cleanup(self):
        calls = {}

        def fake_step(*args, **kwargs):
            calls["step_kwargs"] = kwargs
            return types.SimpleNamespace(
                output="initial", terminal_id="term-1", replayed=False
            )

        def fake_stabilize(**kwargs):
            calls["stabilize"] = kwargs
            return "final answer"

        def fake_cleanup(*args, **kwargs):
            calls["cleanup"] = (args, kwargs)

        original_step = mod.step
        original_stabilize = mod._stabilize_live_step_output
        original_cleanup = mod._cleanup_step_terminal
        mod.step = fake_step
        mod._stabilize_live_step_output = fake_stabilize
        mod._cleanup_step_terminal = fake_cleanup
        try:
            with tempfile.TemporaryDirectory() as temp:
                result = mod._run_stabilized_step(
                    agent="test-agent",
                    prompt="do it",
                    step_id="stable-step",
                    repo=Path(temp),
                    evidence_dir=Path(temp) / "evidence",
                )
            self.assertEqual(result, "final answer")
            self.assertFalse(calls["step_kwargs"]["teardown"])
            self.assertEqual(calls["stabilize"]["terminal_id"], "term-1")
            self.assertIn("cleanup", calls)
        finally:
            mod.step = original_step
            mod._stabilize_live_step_output = original_stabilize
            mod._cleanup_step_terminal = original_cleanup

    def test_live_tui_output_is_classified_as_incomplete(self):
        live = (
            "Reading 1 file…\n\n"
            "✻ Architecting… (2s · ↓ 67 tokens · thinking with high effort)\n"
            "tmux focus-events off · add 'set -g focus-events on' to ~/.tmux.conf"
        )
        self.assertTrue(mod._looks_incomplete_agent_output(live))
        self.assertTrue(mod._has_live_tui_activity(live))

    def test_stale_spinner_before_completion_summary_is_not_live_activity(self):
        settled = (
            "✻ Architecting… (5s · thinking)\n"
            "● {\"ok\": true}\n"
            "✻ Architected for 7s\n"
            "❯"
        )
        self.assertFalse(mod._has_live_tui_activity(settled))

    def test_stabilizer_waits_past_premature_completed_until_answer_is_stable(self):
        snapshots = iter([
            (
                "completed",
                "[NO RESPONSE - agent completed without producing a text response]\nReading 1 file…",
                "✻ Architecting… (2s · thinking)",
            ),
            ("processing", "{\"ok\": true}", "✻ Architecting… (5s · thinking)"),
            ("completed", "{\"ok\": true}", "✻ Architected for 7s\n❯"),
            ("completed", "{\"ok\": true}", "✻ Architected for 7s\n❯"),
        ])
        original_snapshot = mod._cao_terminal_snapshot
        original_wait = mod._wait
        mod._cao_terminal_snapshot = lambda terminal_id: next(snapshots)
        mod._wait = lambda seconds: None
        try:
            with tempfile.TemporaryDirectory() as temp:
                result = mod._stabilize_live_step_output(
                    terminal_id="term-1",
                    initial_output="Reading 1 file…",
                    step_id="s1",
                    evidence_dir=Path(temp),
                )
                self.assertEqual(result, '{"ok": true}')
                evidence = json.loads((Path(temp) / "s1.stabilization.json").read_text())
                self.assertTrue(evidence["stabilized"])
                self.assertEqual(len(evidence["polls"]), 4)
        finally:
            mod._cao_terminal_snapshot = original_snapshot
            mod._wait = original_wait

    def test_json_contract_step_repairs_malformed_completed_response_once(self):
        calls = []
        outputs = iter([
            '{foo: "bar"}',
            '{"foo": "bar"}',
        ])

        def fake_run(**kwargs):
            calls.append(kwargs["step_id"])
            return next(outputs)

        original_run = mod._run_stabilized_step
        mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                value = mod._run_json_contract_step(
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
            mod._run_stabilized_step = original_run

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
                raise mod.WorkflowContractError("foo must be right")
            return value

        original_run = mod._run_stabilized_step
        mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                value = mod._run_json_contract_step(
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
            mod._run_stabilized_step = original_run

    def test_incomplete_execution_never_enters_json_repair(self):
        calls = []

        def fake_run(**kwargs):
            calls.append(kwargs["step_id"])
            raise mod.IncompleteAgentExecutionError("worker still processing")

        original_run = mod._run_stabilized_step
        mod._run_stabilized_step = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(mod.IncompleteAgentExecutionError):
                    mod._run_json_contract_step(
                        agent="test-agent",
                        prompt="Return JSON",
                        label="test",
                        step_id="json-test",
                        repo=Path(temp),
                        evidence_dir=Path(temp) / "evidence",
                    )
                self.assertEqual(calls, ["json-test"])
        finally:
            mod._run_stabilized_step = original_run


if __name__ == "__main__":
    unittest.main()
