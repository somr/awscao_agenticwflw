"""Tests for the CAO worker write-scope security hook.

This hook (.claude/hooks/restrict-write-scope.py) is the actual enforcement
for restricting CAO-spawned agent writes to .agentic-sdlc/runtime/** — see
.agentic-sdlc/cao/workflows/README.md, "Answer file delivery & the
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
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "restrict-write-scope.py"


def run_hook(*, cao_terminal_id: str | None, tool_input: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if cao_terminal_id is None:
        env.pop("CAO_TERMINAL_ID", None)
    else:
        env["CAO_TERMINAL_ID"] = cao_terminal_id
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
