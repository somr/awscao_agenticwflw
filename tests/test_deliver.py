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
import os
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

# Exercise the exact standalone artifact installed into CAO, not a second
# compatibility implementation of the extracted helpers.
sys.path.insert(0, str(ROOT / ".agentic-sdlc" / "cao"))
from build_workflow import build_source
if os.environ.get("SDLC_TEST_SOURCE") == "1":
    mod = importlib.import_module("sdlc_workflows.delivery")
else:
    mod = types.ModuleType("deliver_bundle")
    exec(compile(build_source("deliver"), "<bundled:deliver>", "exec"), mod.__dict__)


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


# Verification commands now come from the registry; these tests only need two of them.
TWO_COMMANDS = [["compile-step", "app"], ["test-step", "app/tests"]]


class RunVerificationTest(unittest.TestCase):
    def test_missing_tool_is_recorded_as_failed_verification(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp, patch.object(mod.subprocess, 'run', side_effect=FileNotFoundError('missing runtime')):
            summary = mod._run_verification(Path(temp), Path(temp) / 'evidence', 'missing', [['spark-submit', 'test.py']])
            self.assertFalse(summary['passed'])
            self.assertIsNone(summary['commands'][0]['returncode'])
            self.assertIn('missing runtime', (Path(temp) / 'evidence/missing-cmd1.log').read_text())

    def test_all_commands_pass(self):
        original_run = mod.subprocess.run
        mod.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "ok", "")
        try:
            with tempfile.TemporaryDirectory() as temp:
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test", TWO_COMMANDS)
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
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test", TWO_COMMANDS)
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
                summary = mod._run_verification(Path(temp), Path(temp) / "evidence", "verify-test", TWO_COMMANDS)
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


def _write_registry(repo: Path, **keys) -> None:
    """A minimal registry: the source-root helpers only read source_roots/write_profiles."""
    (repo / ".agentic-sdlc" / "cao").mkdir(parents=True, exist_ok=True)
    (repo / ".agentic-sdlc" / "cao" / "specialists.json").write_text(json.dumps(keys), encoding="utf-8")


def _committed_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout
    return sorted(out.split())


class ConfigurableSourceRootsDeliveryTest(unittest.TestCase):
    """Delivery commits, checks and prompts by the configured source roots, not a hardcoded app/."""

    def writing_step(self, *files):
        def fake_step(**kwargs):
            for name in files:
                target = kwargs["repo"] / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x = 1\n", encoding="utf-8")
            return {"tasks_completed": ["T1"], "files_changed": list(files), "assumptions": [], "deviations": []}
        return fake_step

    def implement(self, repo):
        return mod._implement_and_commit(prompt="do it", step_id="implement-v1", repo=repo,
                                         evidence_dir=repo / "evidence", ticket_id="T-1", action_label="Implement approved plan")

    def with_step(self, step):
        original = mod._run_json_contract_step
        mod._run_json_contract_step = step
        self.addCleanup(setattr, mod, "_run_json_contract_step", original)

    def make_repo(self, **registry):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        _init_repo(repo)
        if registry:
            _write_registry(repo, **registry)
        return repo

    def test_only_files_under_the_roots_are_committed_and_a_missing_root_is_harmless(self):
        # git add fails on a pathspec that matches nothing, so a root nobody created must be skipped.
        repo = self.make_repo(source_roots=["billing/src", "never/created"])
        self.with_step(self.writing_step("billing/src/A.java", "notes/outside.md"))
        self.implement(repo)
        self.assertEqual(_committed_files(repo), ["billing/src/A.java"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True).stdout
        self.assertIn("?? notes/", status)  # the change outside the roots was left alone

    def test_a_module_created_by_the_worker_is_committed(self):
        repo = self.make_repo(source_roots=["existing/src", "newmod/src"])
        self.with_step(self.writing_step("newmod/src/B.java"))
        self.implement(repo)
        self.assertEqual(_committed_files(repo), ["newmod/src/B.java"])

    def test_changes_only_outside_the_roots_are_an_error_that_names_the_roots(self):
        repo = self.make_repo(source_roots=["billing/src"])
        self.with_step(self.writing_step("notes/x.md"))
        with self.assertRaisesRegex(mod.WorkflowContractError, r"left no changes under the source roots \(billing/src\)"):
            self.implement(repo)

    def test_no_root_exists_and_nothing_written_is_a_clean_error_not_a_git_failure(self):
        repo = self.make_repo(source_roots=["never/created"])
        (repo / "unrelated.txt").write_text("untracked\n", encoding="utf-8")
        self.with_step(self.writing_step())
        with self.assertRaises(mod.WorkflowContractError):
            self.implement(repo)
        # An empty pathspec would have listed the whole repository, including this file.
        self.assertEqual(mod._source_changes(repo, ["never/created"]), "")

    def test_invalid_config_stops_before_the_agent_runs(self):
        for registry in ({"source_roots": [".."]}, {"source_roots": [".git"]}, {"write_profiles": {"sdlc_code_supervisor": None}}):
            repo = self.make_repo(**registry)
            self.with_step(lambda **kwargs: self.fail("the agent must not run with an invalid source-root config"))
            with self.assertRaises(mod.WorkflowContractError):
                self.implement(repo)

    def test_a_symlinked_root_that_leaves_the_repository_stops_before_the_agent(self):
        repo = self.make_repo(source_roots=["escape"])
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.symlink(outside.name, repo / "escape")
        self.with_step(lambda **kwargs: self.fail("the agent must not run"))
        with self.assertRaisesRegex(mod.WorkflowContractError, "outside the repository"):
            self.implement(repo)

    def hybrid(self, repo, *files):
        original = mod.run_hybrid
        mod.run_hybrid = lambda **kwargs: (self.writing_step(*files)(repo=repo), [["check"]], "")
        self.addCleanup(setattr, mod, "run_hybrid", original)
        return mod._hybrid_and_commit(repo=repo, prompt="p", evidence_dir=repo / "evidence", ticket_id="T-1")

    def test_hybrid_ignores_unrelated_dirty_files_but_blocks_on_dirty_roots(self):
        repo = self.make_repo(source_roots=["billing/src"])
        (repo / "README.md").write_text("edited outside the roots\n", encoding="utf-8")
        self.hybrid(repo, "billing/src/A.java")
        self.assertEqual(_committed_files(repo), ["billing/src/A.java"])

        repo = self.make_repo(source_roots=["billing/src"])
        (repo / "billing" / "src").mkdir(parents=True)
        (repo / "billing" / "src" / "dirty.java").write_text("uncommitted\n", encoding="utf-8")
        with self.assertRaisesRegex(mod.WorkflowContractError, r"requires clean source roots \(billing/src\)"):
            self.hybrid(repo, "billing/src/A.java")

    def test_prompts_name_the_roots_and_no_longer_say_app(self):
        default = self.make_repo()
        custom = self.make_repo(source_roots=["billing/src", "billing/test"])
        paths = (Path("plan.md"), Path("contract.md"), Path("governance.md"))
        verification = {"commands": [{"command": ["check"], "passed": False}]}
        for repo, roots in ((default, "app"), (custom, "billing/src, billing/test")):
            prompts = [
                mod.build_implementer_prompt(repo, *paths[:1], *paths[1:]),
                mod.build_implementer_repair_prompt(repo, *paths, verification),
                mod.build_remediator_prompt(repo, paths[0], paths[1], Path("policy.md"), paths[2], [{"id": "F1"}]),
            ]
            for prompt in prompts:
                with self.subTest(roots=roots):
                    self.assertIn(f"Source roots (write only under these): {roots}", prompt)
                    self.assertNotIn("under app/", prompt)


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


class RenderHumanReviewBriefTest(unittest.TestCase):
    def _review(self, findings: list[dict]) -> dict:
        has_dev = any(f["automation_eligibility"] == "DEVELOPER_REQUIRED" for f in findings)
        has_auto = any(f["automation_eligibility"] == "AUTO_FIX" for f in findings)
        return {"summary": "s", "findings": findings, "has_developer_required": has_dev, "has_auto_fix": has_auto}

    def _finding(self, **overrides) -> dict:
        base = {
            "id": "PR-001",
            "file": "app/x.py",
            "location": "somewhere",
            "category": "CORRECTNESS",
            "impact": "MEDIUM",
            "confidence": 0.5,
            "failure_scenario": "bad thing happens",
            "consequence": "breaks",
            "related_acceptance_criterion": "AC-1",
            "remediation_direction": "fix it",
            "automation_eligibility": "DEVELOPER_REQUIRED",
            "reason": "risky",
            "pr_head_sha_reviewed": "deadbeef",
        }
        base.update(overrides)
        return base

    def _verification(self) -> dict:
        return {
            "passed": True,
            "commands": [
                {"command": ["python3", "-m", "compileall", "-q", "app"], "passed": True, "returncode": 0},
                {"command": ["python3", "-m", "unittest", "discover"], "passed": True, "returncode": 0},
            ],
        }

    def test_clean_review_has_no_attention_items(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference=None,
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": ["T1"], "files_changed": ["app/x.py"], "assumptions": [], "deviations": []},
            review=self._review([]),
            remediation_history=[],
            verification=self._verification(),
            convergence_limit_reached=False,
        )
        self.assertIn("No DEVELOPER_REQUIRED findings remain", brief)
        self.assertIn("Findings detected: 0", brief)
        self.assertIn("(local, not yet created)", brief)

    def test_developer_required_finding_appears_in_attention_section(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference="123",
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": []},
            review=self._review([self._finding(impact="HIGH")]),
            remediation_history=[],
            verification=self._verification(),
            convergence_limit_reached=False,
        )
        self.assertIn("PR-001", brief)
        self.assertIn("Remaining/escalated: 1", brief)
        self.assertIn("### 1. PR-001", brief)

    def test_remediation_history_counts_toward_auto_remediated(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference=None,
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": []},
            review=self._review([]),
            remediation_history=[{"round": 1, "findings_addressed": ["PR-005", "PR-006"]}],
            verification=self._verification(),
            convergence_limit_reached=False,
        )
        self.assertIn("Automatically remediated: 2", brief)
        self.assertIn("Autonomous remediation rounds: 1", brief)
        self.assertIn("Findings detected: 2", brief)

    def test_convergence_limit_reached_appears_in_residual_risks(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference=None,
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": []},
            review=self._review([self._finding(automation_eligibility="AUTO_FIX", impact="LOW")]),
            remediation_history=[],
            verification=self._verification(),
            convergence_limit_reached=True,
        )
        self.assertIn("round limit", brief.lower())

    def test_verification_commands_mapped_to_build_and_tests_lines(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference=None,
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": []},
            review=self._review([]),
            remediation_history=[],
            verification=self._verification(),
            convergence_limit_reached=False,
        )
        self.assertIn("Build: `python3 -m compileall -q app` — PASS", brief)
        self.assertIn("Tests: `python3 -m unittest discover` — PASS", brief)

    def test_deviations_appear_as_residual_risks(self):
        brief = mod.render_human_review_brief(
            ticket_id="T-1",
            pr_reference=None,
            pr_head_sha="deadbeef",
            plan_sha256="abc",
            final_completion={"tasks_completed": [], "files_changed": [], "assumptions": [], "deviations": ["could not fix X"]},
            review=self._review([]),
            remediation_history=[],
            verification=self._verification(),
            convergence_limit_reached=False,
        )
        self.assertIn("could not fix X", brief)


class RemediatorCompletionValidatorTest(unittest.TestCase):
    def test_valid_shape_passes(self):
        value = {
            "findings_addressed": ["PR-002"],
            "files_changed": ["app/x.py"],
            "assumptions": [],
            "deviations": [],
        }
        self.assertEqual(mod._remediator_completion_validator(value), value)

    def test_missing_key_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod._remediator_completion_validator({"findings_addressed": []})

    def test_non_string_item_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod._remediator_completion_validator({
                "findings_addressed": [1],
                "files_changed": [],
                "assumptions": [],
                "deviations": [],
            })


class BuildRemediatorPromptTest(unittest.TestCase):
    def test_prompt_embeds_findings_and_paths(self):
        prompt = mod.build_remediator_prompt(
            Path("/repo"),
            Path("/repo/plan.md"),
            Path("/repo/contract.md"),
            Path("/repo/pr-review.md"),
            Path("/repo/governance.md"),
            [{"id": "PR-002", "category": "CORRECTNESS"}],
        )
        self.assertIn("PR-002", prompt)
        self.assertIn(str(Path("/repo/plan.md")), prompt)
        self.assertIn("AUTO_FIX-eligible only", prompt)
        self.assertIn("Do not weaken, skip, or delete", prompt)


class ClassifyPrReviewTest(unittest.TestCase):
    """Encodes .agentic-sdlc/policies/pr-review.md's routing rules as
    executable expectations — this is the policy-as-code boundary, so it
    gets the most thorough coverage in this file."""

    def _finding(self, **overrides) -> dict:
        base = {
            "id": "PR-001",
            "file": "app/payment_service/payment_service.py",
            "location": "process_callback",
            "category": "CORRECTNESS",
            "impact": "LOW",
            "confidence": 0.9,
            "failure_scenario": "x happens",
            "consequence": "y breaks",
            "related_acceptance_criterion": "AC-1",
            "remediation_direction": "do z",
            "localized_and_bounded": True,
            "deterministically_verifiable": True,
            "automation_eligibility": "AUTO_FIX",
            "reason": "small and local",
        }
        base.update(overrides)
        return base

    def test_high_impact_always_forced_to_developer_required(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(impact="HIGH", automation_eligibility="AUTO_FIX")]},
            pr_head_sha="sha1",
        )
        finding = result["findings"][0]
        self.assertEqual(finding["automation_eligibility"], "DEVELOPER_REQUIRED")
        self.assertTrue(finding["policy_overrode_agent_classification"])
        self.assertTrue(result["has_developer_required"])
        self.assertFalse(result["has_auto_fix"])

    def test_protected_category_forced_regardless_of_low_impact(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(impact="LOW", category="ARCHITECTURE", automation_eligibility="AUTO_FIX")]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_medium_honored_as_auto_fix_when_all_conditions_met(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(
                impact="MEDIUM", confidence=0.95, localized_and_bounded=True,
                deterministically_verifiable=True, automation_eligibility="AUTO_FIX",
            )]},
            pr_head_sha="sha1",
        )
        finding = result["findings"][0]
        self.assertEqual(finding["automation_eligibility"], "AUTO_FIX")
        self.assertFalse(finding["policy_overrode_agent_classification"])

    def test_medium_forced_when_confidence_below_threshold(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(
                impact="MEDIUM", confidence=0.5, localized_and_bounded=True,
                deterministically_verifiable=True, automation_eligibility="AUTO_FIX",
            )]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_medium_forced_when_not_localized(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(
                impact="MEDIUM", confidence=0.95, localized_and_bounded=False,
                deterministically_verifiable=True, automation_eligibility="AUTO_FIX",
            )]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_medium_forced_when_not_deterministically_verifiable(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(
                impact="MEDIUM", confidence=0.95, localized_and_bounded=True,
                deterministically_verifiable=False, automation_eligibility="AUTO_FIX",
            )]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_medium_protected_category_still_forced(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(
                impact="MEDIUM", category="CONCURRENCY", confidence=0.99,
                localized_and_bounded=True, deterministically_verifiable=True,
                automation_eligibility="AUTO_FIX",
            )]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_low_impact_honors_agents_auto_fix_claim(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(impact="LOW", automation_eligibility="AUTO_FIX")]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "AUTO_FIX")
        self.assertTrue(result["has_auto_fix"])

    def test_low_impact_honors_agents_developer_required_claim(self):
        result = mod.classify_pr_review(
            {"summary": "s", "findings": [self._finding(impact="LOW", automation_eligibility="DEVELOPER_REQUIRED")]},
            pr_head_sha="sha1",
        )
        self.assertEqual(result["findings"][0]["automation_eligibility"], "DEVELOPER_REQUIRED")

    def test_pr_head_sha_is_always_overwritten_with_the_supplied_value(self):
        finding = self._finding()
        finding["pr_head_sha_reviewed"] = "something-the-agent-made-up"
        result = mod.classify_pr_review({"summary": "s", "findings": [finding]}, pr_head_sha="real-sha")
        self.assertEqual(result["findings"][0]["pr_head_sha_reviewed"], "real-sha")

    def test_empty_findings_list_is_clean(self):
        result = mod.classify_pr_review({"summary": "clean", "findings": []}, pr_head_sha="sha1")
        self.assertEqual(result["findings"], [])
        self.assertFalse(result["has_developer_required"])
        self.assertFalse(result["has_auto_fix"])

    def test_invalid_category_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod.classify_pr_review(
                {"summary": "s", "findings": [self._finding(category="NOT_A_REAL_CATEGORY")]},
                pr_head_sha="sha1",
            )

    def test_invalid_impact_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod.classify_pr_review(
                {"summary": "s", "findings": [self._finding(impact="CRITICAL")]},
                pr_head_sha="sha1",
            )

    def test_confidence_out_of_range_is_rejected(self):
        with self.assertRaises(mod.WorkflowContractError):
            mod.classify_pr_review(
                {"summary": "s", "findings": [self._finding(confidence=1.5)]},
                pr_head_sha="sha1",
            )

    def test_missing_summary_gets_a_placeholder(self):
        result = mod.classify_pr_review({"findings": []}, pr_head_sha="sha1")
        self.assertEqual(result["summary"], "(no summary provided)")


class PrRenderingTest(unittest.TestCase):
    def _completion(self, **overrides) -> dict:
        base = {
            "tasks_completed": ["T1", "T2"],
            "files_changed": ["app/payment_service/payment_service.py"],
            "assumptions": ["used a 30s busy timeout"],
            "deviations": [],
        }
        base.update(overrides)
        return base

    def _verification(self) -> dict:
        return {
            "passed": True,
            "commands": [
                {"command": ["python3", "-m", "compileall", "-q", "app"], "passed": True, "returncode": 0},
                {"command": ["python3", "-m", "unittest", "discover"], "passed": True, "returncode": 0},
            ],
        }

    def test_pr_title_includes_ticket_id(self):
        self.assertIn("PAY-DEMO-001", mod.render_pr_title("PAY-DEMO-001"))

    def test_pr_body_includes_all_key_facts(self):
        body = mod.render_pr_body(
            ticket_id="PAY-DEMO-001",
            plan_path=Path("development-plan.md"),
            plan_sha256="abc123",
            completion=self._completion(),
            verification=self._verification(),
            pr_head_sha="deadbeef",
        )
        for expected in ("PAY-DEMO-001", "T1", "T2", "abc123", "deadbeef", "payment_service.py", "busy timeout", "PASS"):
            self.assertIn(expected, body)

    def test_pr_body_handles_empty_lists_without_crashing(self):
        body = mod.render_pr_body(
            ticket_id="PAY-DEMO-001",
            plan_path=Path("development-plan.md"),
            plan_sha256="abc123",
            completion=self._completion(files_changed=[], assumptions=[], deviations=[]),
            verification={"passed": True, "commands": []},
            pr_head_sha="deadbeef",
        )
        self.assertIn("(none reported)", body)
        self.assertIn("(no verification evidence)", body)

    def test_pr_body_shows_failing_commands_as_fail(self):
        verification = self._verification()
        verification["commands"][1]["passed"] = False
        verification["commands"][1]["returncode"] = 1
        body = mod.render_pr_body(
            ticket_id="PAY-DEMO-001",
            plan_path=Path("development-plan.md"),
            plan_sha256="abc123",
            completion=self._completion(),
            verification=verification,
            pr_head_sha="deadbeef",
        )
        self.assertIn("FAIL", body)


class ComputeDeliveryDiffTest(unittest.TestCase):
    def test_diff_shows_changes_on_the_delivery_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            mod._ensure_delivery_branch(repo, "sdlc/T-1", "main")
            (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
            _run(["add", "app.py"], repo)
            _run(["commit", "-q", "-m", "add app.py"], repo)

            diff = mod._compute_delivery_diff(repo, "main", "sdlc/T-1")
            self.assertIn("app.py", diff)
            self.assertIn("print('hi')", diff)

    def test_diff_is_empty_when_branches_match(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo(repo)
            mod._ensure_delivery_branch(repo, "sdlc/T-1", "main")
            diff = mod._compute_delivery_diff(repo, "main", "sdlc/T-1")
            self.assertEqual(diff.strip(), "")


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
