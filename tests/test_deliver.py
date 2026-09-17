"""Tests for the Delivery Workflow 2 orchestration script.

Same testing approach as tests/test_dev_plan.py: stub sys.modules["cao_workflow"]
so deliver.py can be loaded from its real on-disk path without a live CAO
server, then unit-test individual helpers (monkeypatching where a helper
talks to CAO or forks a subprocess) plus the git-wrapping logic against a
real temporary git repository — the git-branch-rooting bug found in Stage 4
happened exactly in code like this, so it is tested against real git rather
than a mocked one.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".agentic-sdlc" / "cao" / "workflows" / "deliver.py"

stub = types.ModuleType("cao_workflow")
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault("cao_workflow", stub)

spec = importlib.util.spec_from_file_location("deliver", WORKFLOW)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _init_repo(repo: Path) -> str:
    """Create a minimal real git repo with one commit; return its SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    _run(["init", "-q", "-b", "main"], repo)
    _run(["config", "user.email", "test@example.com"], repo)
    _run(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _run(["add", "README.md"], repo)
    _run(["commit", "-q", "-m", "initial"], repo)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()


class ImplementerCompletionValidatorTest(unittest.TestCase):
    def test_valid_shape_passes(self):
        value = {
            "tasks_completed": ["T1"],
            "files_changed": ["app/x.py"],
            "assumptions": [],
            "deviations": [],
        }
        self.assertEqual(mod._implementer_completion_validator(value), value)

    def test_missing_key_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod._implementer_completion_validator({"tasks_completed": []})

    def test_non_string_item_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod._implementer_completion_validator({
                "tasks_completed": [1],
                "files_changed": [],
                "assumptions": [],
                "deviations": [],
            })

    def test_non_dict_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod._implementer_completion_validator(["not", "a", "dict"])


class RunVerificationTest(unittest.TestCase):
    def test_all_commands_pass(self):
        original_run = mod.subprocess.run
        mod.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "ok", "")
        try:
            with tempfile.TemporaryDirectory() as temp:
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test")
                self.assertTrue(summary["passed"])
                self.assertTrue(all(c["passed"] for c in summary["commands"]))
                self.assertTrue((Path(temp) / "evidence" / "verify-test.json").is_file())
                self.assertTrue((Path(temp) / "evidence" / "verify-test-cmd1.log").is_file())
        finally:
            mod.subprocess.run = original_run

    def test_one_command_failing_fails_the_whole_summary(self):
        original_run = mod.subprocess.run
        calls = {"n": 0}

        def fake_run(*a, **k):
            calls["n"] += 1
            code = 0 if calls["n"] == 1 else 1
            return subprocess.CompletedProcess(a[0], code, "out", "err")

        mod.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test")
                self.assertFalse(summary["passed"])
                self.assertTrue(summary["commands"][0]["passed"])
                self.assertFalse(summary["commands"][1]["passed"])
        finally:
            mod.subprocess.run = original_run

    def test_timeout_counts_as_failure(self):
        original_run = mod.subprocess.run

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=1, output="partial", stderr="")

        mod.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as temp:
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test")
                self.assertFalse(summary["passed"])
                self.assertIsNone(summary["commands"][0]["returncode"])
        finally:
            mod.subprocess.run = original_run


class ImplementAndCommitTest(unittest.TestCase):
    def test_changes_under_app_are_committed(self):
        original_step = mod._run_json_contract_step

        def fake_step(**kwargs):
            (kwargs["repo"] / "app").mkdir(exist_ok=True)
            (kwargs["repo"] / "app" / "new_file.py").write_text("x = 1\n", encoding="utf-8")
            return {"tasks_completed": ["T1"], "files_changed": ["app/new_file.py"], "assumptions": [], "deviations": []}

        mod._run_json_contract_step = fake_step
        try:
            with tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                _init_repo(repo)
                completion = mod._implement_and_commit(
                    prompt="do it",
                    step_id="implement-v1",
                    repo=repo,
                    evidence_dir=repo / "evidence",
                    ticket_id="T-1",
                    action_label="Implement approved plan",
                )
                self.assertEqual(completion["tasks_completed"], ["T1"])
                log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=str(repo), capture_output=True, text=True).stdout
                self.assertIn("Implement approved plan", log)
                self.assertIn("T1", log)
        finally:
            mod._run_json_contract_step = original_step

    def test_no_changes_raises(self):
        original_step = mod._run_json_contract_step
        mod._run_json_contract_step = lambda **kwargs: {
            "tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": []
        }
        try:
            with tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                _init_repo(repo)
                with self.assertRaises(mod.WorkflowContractError):
                    mod._implement_and_commit(
                        prompt="do it",
                        step_id="implement-v1",
                        repo=repo,
                        evidence_dir=repo / "evidence",
                        ticket_id="T-1",
                        action_label="Implement approved plan",
                    )
        finally:
            mod._run_json_contract_step = original_step


class GitBranchRootingTest(unittest.TestCase):
    """Regression coverage for the Stage 4 bug: the delivery branch must
    root from the CURRENT tip of base_branch, never from a historical
    baseline commit — see deliver.py's _ensure_delivery_branch docstring."""

    def test_branch_created_from_current_base_branch_tip_not_an_old_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            old_sha = _init_repo(repo)
            (repo / "README.md").write_text("updated\n", encoding="utf-8")
            _run(["add", "README.md"], repo)
            _run(["commit", "-q", "-m", "second commit"], repo)
            new_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
            self.assertNotEqual(old_sha, new_sha)

            mod._ensure_delivery_branch(repo, "sdlc/T-1", "main")
            branch_sha = subprocess.run(
                ["git", "rev-parse", "sdlc/T-1"], cwd=str(repo), capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(branch_sha, new_sha, "delivery branch must root from the current base_branch tip")

    def test_resuming_an_existing_branch_checks_it_out_without_recreating(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            mod._ensure_delivery_branch(repo, "sdlc/T-1", "main")
            _run(["checkout", "main"], repo)
            (repo / "app_marker.txt").write_text("only on delivery branch\n", encoding="utf-8")
            _run(["checkout", "sdlc/T-1"], repo)
            _run(["add", "app_marker.txt"], repo)
            _run(["commit", "-q", "-m", "delivery-only work"], repo)
            before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()

            mod._ensure_delivery_branch(repo, "sdlc/T-1", "main")
            after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
            self.assertEqual(before, after, "resuming an existing delivery branch must not discard its commits")


class BaselineIsAncestorTest(unittest.TestCase):
    def test_true_when_baseline_is_an_ancestor(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            old_sha = _init_repo(repo)
            (repo / "README.md").write_text("updated\n", encoding="utf-8")
            _run(["add", "README.md"], repo)
            _run(["commit", "-q", "-m", "second commit"], repo)
            self.assertTrue(mod._baseline_is_ancestor(repo, old_sha, "main"))

    def test_false_for_an_unrelated_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            fake_sha = "0" * 40
            self.assertFalse(mod._baseline_is_ancestor(repo, fake_sha, "main"))


class CheckPlanApprovedTest(unittest.TestCase):
    def _write_plan(self, records_dir: Path, content: str = "the plan\n") -> tuple[Path, str]:
        records_dir.mkdir(parents=True, exist_ok=True)
        plan_path = records_dir / "development-plan.md"
        plan_path.write_text(content, encoding="utf-8")
        return plan_path, mod._sha256_file(plan_path)

    def test_missing_manifest_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            records_dir = repo / "records"
            plan_path, _ = self._write_plan(records_dir)
            with self.assertRaises(mod.WorkflowContractError):
                mod._check_plan_approved(records_dir, plan_path, repo, "main")

    def test_unapproved_state_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            baseline = _init_repo(repo)
            records_dir = repo / "records"
            plan_path, plan_sha = self._write_plan(records_dir)
            mod._write_json(records_dir / "execution-manifest.json", {
                "state": "AWAITING_HUMAN_APPROVAL", "plan_sha256": plan_sha, "repository_baseline_sha": baseline,
            })
            mod._write_json(records_dir / "plan-approval-record.json", {"decision": "APPROVED", "plan_sha256": plan_sha})
            with self.assertRaises(mod.WorkflowContractError):
                mod._check_plan_approved(records_dir, plan_path, repo, "main")

    def test_plan_hash_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            baseline = _init_repo(repo)
            records_dir = repo / "records"
            plan_path, plan_sha = self._write_plan(records_dir)
            mod._write_json(records_dir / "execution-manifest.json", {
                "state": "APPROVED", "plan_sha256": plan_sha, "repository_baseline_sha": baseline,
            })
            mod._write_json(records_dir / "plan-approval-record.json", {"decision": "APPROVED", "plan_sha256": plan_sha})
            plan_path.write_text("the plan, modified after approval\n", encoding="utf-8")
            with self.assertRaises(mod.WorkflowContractError):
                mod._check_plan_approved(records_dir, plan_path, repo, "main")

    def test_baseline_not_an_ancestor_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            records_dir = repo / "records"
            plan_path, plan_sha = self._write_plan(records_dir)
            fake_baseline = "0" * 40
            mod._write_json(records_dir / "execution-manifest.json", {
                "state": "APPROVED", "plan_sha256": plan_sha, "repository_baseline_sha": fake_baseline,
            })
            mod._write_json(records_dir / "plan-approval-record.json", {"decision": "APPROVED", "plan_sha256": plan_sha})
            with self.assertRaises(mod.WorkflowContractError):
                mod._check_plan_approved(records_dir, plan_path, repo, "main")

    def test_valid_approved_plan_returns_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            baseline = _init_repo(repo)
            records_dir = repo / "records"
            plan_path, plan_sha = self._write_plan(records_dir)
            manifest = {"state": "APPROVED", "plan_sha256": plan_sha, "repository_baseline_sha": baseline}
            mod._write_json(records_dir / "execution-manifest.json", manifest)
            mod._write_json(records_dir / "plan-approval-record.json", {"decision": "APPROVED", "plan_sha256": plan_sha})
            result = mod._check_plan_approved(records_dir, plan_path, repo, "main")
            self.assertEqual(result["repository_baseline_sha"], baseline)


if __name__ == "__main__":
    unittest.main()
