"""Tests for the CAO worker write-scope security hook.

This hook (.claude/hooks/restrict-write-scope.py) is the actual enforcement
for restricting CAO-spawned agent writes to .agentic-sdlc/runtime/** — see
docs/workflows/planning.md, "Answer file delivery & the
write-scope hook", for why Claude Code's own permissions.allow/deny rules do
not apply here (CAO always launches workers with --dangerously-skip-permissions,
which bypasses that layer but not PreToolUse hooks).

These tests invoke the hook script as a subprocess with controlled stdin and
environment, exactly as Claude Code itself does when the hook fires, so a
regression here is caught the same way a real misconfiguration would surface.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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
        cwd=str(ROOT),
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
                "sdlc-records/PAY-DEMO-001/execution-manifest.json",
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


if __name__ == "__main__":
    unittest.main()
