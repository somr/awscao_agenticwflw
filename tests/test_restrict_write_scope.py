"""Tests for the CAO worker write-scope security hook.

This hook (.claude/hooks/restrict-write-scope.py) is the actual enforcement
for restricting CAO-spawned agent writes to .agentic-sdlc/runtime/** — see
agentic-sdlc-docs/reference/write-scope-hook.md, for why Claude Code's own permissions.allow/deny rules do
not apply here (CAO always launches workers with --dangerously-skip-permissions,
which bypasses that layer but not PreToolUse hooks).

These tests invoke the hook script as a subprocess with controlled stdin and
environment, exactly as Claude Code itself does when the hook fires, so a
regression here is caught the same way a real misconfiguration would surface.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "restrict-write-scope.py"


class _FakeTerminalServer:
    """Stands in for cao-server's GET /terminals/{id} for one test.

    Always returns the same canned agent_profile regardless of the terminal
    id requested — good enough for exercising the hook's own decision logic,
    which is all these tests are targeting.
    """

    def __init__(self, agent_profile: str | None, status: int = 200):
        self._agent_profile = agent_profile
        self._status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
                if outer._agent_profile is None:
                    body = b"not json{{{"
                else:
                    body = json.dumps({"agent_profile": outer._agent_profile}).encode()
                self.send_response(outer._status)
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


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_hook(
    *,
    cao_terminal_id: str | None,
    tool_input: dict,
    cao_api_port: int | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if cao_terminal_id is None:
        env.pop("CAO_TERMINAL_ID", None)
    else:
        env["CAO_TERMINAL_ID"] = cao_terminal_id
    if cao_api_port is not None:
        env["CAO_API_HOST"] = "127.0.0.1"
        env["CAO_API_PORT"] = str(cao_api_port)
        env.pop("CAO_API_BASE_URL", None)
    payload = json.dumps({"tool_input": tool_input})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or ROOT),
        timeout=10,
    )


def assert_allowed(test: unittest.TestCase, result: subprocess.CompletedProcess) -> None:
    test.assertEqual(result.returncode, 0, result.stderr)
    test.assertEqual(result.stdout.strip(), "", "expected no hook output (allow), got a decision")


def assert_denied(test: unittest.TestCase, result: subprocess.CompletedProcess) -> None:
    test.assertEqual(result.returncode, 0, result.stderr)
    decision = json.loads(result.stdout)
    test.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")


class WriteScopeHookTest(unittest.TestCase):
    def test_non_cao_session_is_never_restricted(self):
        # No CAO_TERMINAL_ID means this isn't a CAO-spawned worker (e.g. the
        # human's own interactive session) — must never be restricted.
        assert_allowed(self, run_hook(cao_terminal_id=None, tool_input={"file_path": "README.md"}))

    def test_cao_worker_in_scope_write_is_allowed(self):
        assert_allowed(
            self,
            run_hook(cao_terminal_id="term-1", tool_input={"file_path": ".agentic-sdlc/runtime/foo/bar.json"}),
        )

    def test_cao_worker_out_of_scope_write_is_denied(self):
        assert_denied(self, run_hook(cao_terminal_id="term-1", tool_input={"file_path": "README.md"}))

    def test_cao_worker_path_traversal_is_denied(self):
        assert_denied(
            self,
            run_hook(
                cao_terminal_id="term-1",
                tool_input={"file_path": ".agentic-sdlc/runtime/../../etc/passwd"},
            ),
        )

    def test_cao_worker_absolute_path_outside_repo_is_denied(self):
        assert_denied(self, run_hook(cao_terminal_id="term-1", tool_input={"file_path": "/tmp/evil.txt"}))

    def test_cao_worker_notebook_path_in_scope_is_allowed(self):
        assert_allowed(
            self,
            run_hook(cao_terminal_id="term-1", tool_input={"notebook_path": ".agentic-sdlc/runtime/x.ipynb"}),
        )

    def test_cao_worker_notebook_path_out_of_scope_is_denied(self):
        assert_denied(
            self,
            run_hook(cao_terminal_id="term-1", tool_input={"notebook_path": "notebooks/evil.ipynb"}),
        )

    def test_missing_file_path_is_a_no_op(self):
        assert_allowed(self, run_hook(cao_terminal_id="term-1", tool_input={}))

    def test_implementer_profile_may_write_under_app(self):
        with _FakeTerminalServer("sdlc_implementer") as port:
            assert_allowed(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/payment_service/payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_remediator_profile_may_write_under_app(self):
        with _FakeTerminalServer("sdlc_remediator") as port:
            assert_allowed(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/tests/test_payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_supervisor_cannot_modify_application_or_registry(self):
        with _FakeTerminalServer("sdlc_code_supervisor") as port:
            for path in ("app/value.py", ".agentic-sdlc/cao/specialists.json"):
                assert_denied(self, run_hook(cao_terminal_id="term-1", tool_input={"file_path": path}, cao_api_port=port))

    def test_non_widened_profile_still_cannot_write_under_app(self):
        with _FakeTerminalServer("sdlc_context_normalizer") as port:
            assert_denied(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/payment_service/payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_unrecognized_profile_still_cannot_write_under_app(self):
        with _FakeTerminalServer("some_future_profile_nobody_widened_yet") as port:
            assert_denied(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/payment_service/payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_deny_list_wins_over_a_widened_profile(self):
        # Even sdlc_implementer must never be able to rewrite its own
        # guardrails (the hook, its wiring, or published governance records).
        with _FakeTerminalServer("sdlc_implementer") as port:
            for path in (
                ".claude/hooks/restrict-write-scope.py",
                ".claude/settings.json",
                ".agentic-sdlc/policies/governance.md",
                "agentic-sdlc-records/PAY-DEMO-001/execution-manifest.json",
                ".agentic-sdlc/cao/profiles/implementer.md",
            ):
                with self.subTest(path=path):
                    assert_denied(
                        self,
                        run_hook(cao_terminal_id="term-1", tool_input={"file_path": path}, cao_api_port=port),
                    )

    def test_implementer_profile_still_keeps_the_answer_file_channel(self):
        with _FakeTerminalServer("sdlc_implementer") as port:
            assert_allowed(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": ".agentic-sdlc/runtime/PAY-DEMO-001/plan-1/implement-v1.answer.json"},
                    cao_api_port=port,
                ),
            )

    def test_widening_fails_closed_on_http_error_status(self):
        with _FakeTerminalServer("sdlc_implementer", status=500) as port:
            assert_denied(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/payment_service/payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_widening_fails_closed_on_malformed_response_body(self):
        with _FakeTerminalServer(None) as port:  # server returns non-JSON body
            assert_denied(
                self,
                run_hook(
                    cao_terminal_id="term-1",
                    tool_input={"file_path": "app/payment_service/payment_service.py"},
                    cao_api_port=port,
                ),
            )

    def test_widening_fails_closed_on_connection_error(self):
        assert_denied(
            self,
            run_hook(
                cao_terminal_id="term-1",
                tool_input={"file_path": "app/payment_service/payment_service.py"},
                cao_api_port=_unused_port(),  # nothing listening
            ),
        )

    def test_dev_plan_answer_paths_all_pass_the_hook(self):
        # Cross-check against the real path shape dev_plan.py builds
        # (evidence_dir / f"{step_id}{answer_suffix}" under
        # .agentic-sdlc/runtime/<ticket>/<run_id>/...) so a change to either
        # the hook's allowed root or dev_plan.py's evidence layout that
        # breaks the pairing is caught here instead of in a live run.
        evidence_dir = Path(".agentic-sdlc/runtime/PAY-DEMO-001/plan-1/context/normalized/agent-output")
        for step_id, suffix in [
            ("context-normalize-v1", ".answer.json"),
            ("planning-analysis-v1", ".answer.md"),
            ("plan-author-r1-c1", ".answer.md"),
            ("plan-review-r1-c1", ".answer.json"),
        ]:
            answer_path = evidence_dir / f"{step_id}{suffix}"
            with self.subTest(answer_path=str(answer_path)):
                assert_allowed(
                    self,
                    run_hook(cao_terminal_id="term-1", tool_input={"file_path": str(answer_path)}),
                )


def denial_reason(result: subprocess.CompletedProcess) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


_HOOK_SPEC = importlib.util.spec_from_file_location("restrict_write_scope_under_test", HOOK)
_HOOK_MODULE = importlib.util.module_from_spec(_HOOK_SPEC)
_HOOK_SPEC.loader.exec_module(_HOOK_MODULE)


def run_hook_in_process(*, repo: Path, profile: str | None, path: str) -> subprocess.CompletedProcess:
    """Run the hook's main() in this process with the CAO profile lookup stubbed.

    Same decision logic as the subprocess tests, without a Python start-up per check, so wide
    matrices stay fast. The profile lookup over HTTP is covered by the subprocess tests.
    """
    out = io.StringIO()
    with mock.patch.dict(os.environ, {"CAO_TERMINAL_ID": "term-1"}), \
            mock.patch.object(_HOOK_MODULE, "_fetch_agent_profile", return_value=profile), \
            mock.patch("os.getcwd", return_value=str(repo)), \
            mock.patch("sys.stdin", io.StringIO(json.dumps({"tool_input": {"file_path": path}}))), \
            contextlib.redirect_stdout(out):
        _HOOK_MODULE.main()
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=out.getvalue(), stderr="")


class ConfigurableSourceRootsHookTest(unittest.TestCase):
    """The roots a widened profile may write come from the registry; anything invalid fails closed."""

    ANSWER = ".agentic-sdlc/runtime/T-1/run-1/implement-v1.answer.json"

    def make_repo(self, registry=None, raw_text=None) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name).resolve()
        (repo / ".agentic-sdlc/cao").mkdir(parents=True)
        if registry is not None:
            (repo / ".agentic-sdlc/cao/specialists.json").write_text(json.dumps(registry))
        if raw_text is not None:
            (repo / ".agentic-sdlc/cao/specialists.json").write_bytes(raw_text)
        return repo

    def check(self, repo, profile, paths, *, allowed, real=False):
        """real=True runs the hook as a subprocess against a fake CAO server, as Claude Code does."""
        assertion = assert_allowed if allowed else assert_denied
        if real:
            with _FakeTerminalServer(profile) as port:
                for path in paths:
                    with self.subTest(profile=profile, path=path, real=True):
                        assertion(self, run_hook(cao_terminal_id="term-1", tool_input={"file_path": path},
                                                 cao_api_port=port, cwd=repo))
            return
        for path in paths:
            with self.subTest(profile=profile, path=path):
                assertion(self, run_hook_in_process(repo=repo, profile=profile, path=path))

    def reason(self, repo, profile, path):
        return denial_reason(run_hook_in_process(repo=repo, profile=profile, path=path))

    def test_configured_roots_replace_app_for_implementer_and_remediator(self):
        repo = self.make_repo({"source_roots": ["billing/src", "billing/test"]})
        allowed = ["billing/src/A.java", "billing/test/deep/T.java", "billing/src/newpkg/New.java", self.ANSWER]
        denied = ["app/x.py", "billing/other/x.java", "billing/srcx/y.java", "billing/README.md",
                  "billing/src/../../app/x.py"]
        for profile in ("sdlc_implementer", "sdlc_remediator"):
            self.check(repo, profile, allowed, allowed=True)
            self.check(repo, profile, denied, allowed=False)
        # The same decisions as a real subprocess, exactly as Claude Code fires the hook.
        self.check(repo, "sdlc_implementer", ["billing/src/A.java"], allowed=True, real=True)
        self.check(repo, "sdlc_implementer", ["app/x.py", "billing/srcx/y.java"], allowed=False, real=True)

    def test_a_root_that_does_not_exist_yet_can_be_created(self):
        repo = self.make_repo({"source_roots": ["newmodule/src"]})
        self.assertFalse((repo / "newmodule").exists())
        self.check(repo, "sdlc_implementer", ["newmodule/src/pkg/deep/New.java"], allowed=True)

    def test_defaults_apply_without_a_registry_or_without_the_keys(self):
        for repo in (self.make_repo(), self.make_repo({"version": 1, "workers": {}})):
            self.check(repo, "sdlc_implementer", ["app/x.py", self.ANSWER], allowed=True)
            self.check(repo, "sdlc_implementer", ["src/x.py", "other/x.py"], allowed=False)
            self.check(repo, "sdlc_remediator", ["app/x.py"], allowed=True)
        self.check(self.make_repo(), "sdlc_implementer", ["app/x.py"], allowed=True, real=True)

    def test_a_specialist_profile_can_be_limited_to_part_of_a_root(self):
        repo = self.make_repo({"source_roots": ["billing"],
                               "write_profiles": {"sdlc_implementer": None, "sdlc_java": ["billing/src"]}})
        self.check(repo, "sdlc_java", ["billing/src/A.java", self.ANSWER], allowed=True)
        self.check(repo, "sdlc_java", ["billing/test/T.java", "billing/README.md", "app/x.py"], allowed=False)
        self.check(repo, "sdlc_implementer", ["billing/test/T.java", "billing/src/A.java"], allowed=True)
        # Not listed in write_profiles: no source root at all, even though the config is valid.
        self.check(repo, "sdlc_remediator", ["billing/src/A.java"], allowed=False)

    def test_read_only_and_unknown_profiles_never_get_source_roots(self):
        repo = self.make_repo({"source_roots": ["billing/src"]})
        for profile in ("sdlc_code_supervisor", "sdlc_pr_reviewer", "sdlc_plan_author",
                        "sdlc_source_mapper", "some_other_profile", None):
            self.check(repo, profile, ["billing/src/A.java"], allowed=False)
            self.check(repo, profile, [self.ANSWER], allowed=True)  # the answer-file channel is unchanged

    def test_a_present_but_broken_registry_grants_nothing_to_any_profile(self):
        base = {"source_roots": ["billing/src"]}
        cases = {
            "malformed json": {"raw_text": b"{not json"},
            "json array": {"raw_text": b"[]"},
            "invalid utf-8": {"raw_text": b"\xff\xfe"},
            "parent segment": {"registry": {"source_roots": ["../elsewhere"]}},
            "the git directory": {"registry": {"source_roots": [".git"]}},
            "supervisor listed": {"registry": dict(base, write_profiles={"sdlc_code_supervisor": None})},
            "read-only prefix listed": {"registry": dict(base, write_profiles={"sdlc_source_mapper": None})},
            "profile root outside the roots": {"registry": dict(base, write_profiles={"sdlc_implementer": ["other"]})},
            "no profiles at all": {"registry": dict(base, write_profiles={})},
        }
        for name, kwargs in cases.items():
            repo = self.make_repo(**kwargs)
            for profile in ("sdlc_implementer", "sdlc_remediator"):
                # Neither the configured root nor the default app/ may be used as a fallback.
                self.check(repo, profile, ["billing/src/A.java", "app/x.py"], allowed=False)
                self.check(repo, profile, [self.ANSWER], allowed=True)
            self.assertIn("invalid", self.reason(repo, "sdlc_implementer", "billing/src/A.java"), name)
        # One real subprocess run of the most likely mistake, a malformed file.
        repo = self.make_repo(raw_text=b"{not json")
        self.check(repo, "sdlc_implementer", ["app/x.py"], allowed=False, real=True)

    def test_a_registry_that_is_a_directory_grants_nothing(self):
        repo = self.make_repo()
        (repo / ".agentic-sdlc/cao/specialists.json").mkdir()
        self.check(repo, "sdlc_implementer", ["app/x.py"], allowed=False)

    def test_a_symlinked_root_that_leaves_the_repository_invalidates_the_config(self):
        repo = self.make_repo({"source_roots": ["billing/src", "escape"]})
        outside = repo.parent / (repo.name + "-outside")
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        os.symlink(outside, repo / "escape")
        # The good root next to the bad one is refused too: the whole config is invalid.
        self.check(repo, "sdlc_implementer", ["billing/src/A.java", "escape/x.txt"], allowed=False)
        self.assertIn("outside the repository", self.reason(repo, "sdlc_implementer", "billing/src/A.java"))
        self.check(repo, "sdlc_implementer", ["billing/src/A.java"], allowed=False, real=True)

    def test_a_symlinked_root_into_an_sdlc_folder_invalidates_the_config(self):
        repo = self.make_repo({"source_roots": ["gitlink"]})
        (repo / ".git").mkdir()
        os.symlink(repo / ".git", repo / "gitlink")
        self.check(repo, "sdlc_implementer", ["gitlink/config", ".git/config"], allowed=False)

    def test_guardrails_and_the_registry_stay_unwritable_for_every_widened_profile(self):
        repo = self.make_repo({"source_roots": ["billing/src"],
                               "write_profiles": {"sdlc_implementer": None, "sdlc_remediator": None,
                                                  "sdlc_java": ["billing/src"]}})
        for profile in ("sdlc_implementer", "sdlc_remediator", "sdlc_java"):
            self.check(repo, profile, [".agentic-sdlc/cao/specialists.json", ".agentic-sdlc/cao/profiles/implementer.md",
                                       ".claude/settings.json", ".claude/hooks/restrict-write-scope.py",
                                       ".git/config", "agentic-sdlc-records/T-1/development-plan.md"], allowed=False)
        self.check(repo, "sdlc_implementer", [".agentic-sdlc/cao/specialists.json"], allowed=False, real=True)

    def test_a_failed_profile_lookup_grants_nothing_even_with_a_valid_custom_config(self):
        repo = self.make_repo({"source_roots": ["billing/src"]})
        with _FakeTerminalServer("sdlc_implementer", status=500) as port:
            assert_denied(self, run_hook(cao_terminal_id="term-1", tool_input={"file_path": "billing/src/A.java"},
                                         cao_api_port=port, cwd=repo))

    def test_the_real_repository_registry_is_valid_and_keeps_the_sample_app_writable(self):
        self.check(ROOT, "sdlc_implementer", ["app/payment_service/payment_service.py"], allowed=True, real=True)
        self.check(ROOT, "sdlc_code_supervisor", ["app/payment_service/payment_service.py"], allowed=False)


if __name__ == "__main__":
    unittest.main()
