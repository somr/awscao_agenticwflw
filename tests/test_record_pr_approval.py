from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agentic-sdlc" / "scripts" / "record_pr_approval.py"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _init_repo_with_delivery_branch(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "test@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    assert _git(["add", "README.md"], repo).returncode == 0
    assert _git(["commit", "-q", "-m", "initial"], repo).returncode == 0
    assert _git(["checkout", "-q", "-b", "sdlc/T-1"], repo).returncode == 0
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
    return head


def _write_manifest(records: Path, **fields) -> None:
    records.mkdir(parents=True, exist_ok=True)
    base = {
        "schema_version": "1.0",
        "ticket_id": "T-1",
        "delivery_branch": "sdlc/T-1",
        "state": "AWAITING_HUMAN_REVIEW",
    }
    base.update(fields)
    (records / "delivery-manifest.json").write_text(json.dumps(base), encoding="utf-8")


class RecordPrApprovalTest(unittest.TestCase):
    def test_approval_binds_to_exact_pr_head_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = _init_repo_with_delivery_branch(repo)
            records = repo / "sdlc-records" / "T-1"
            _write_manifest(records, pr_head_sha=head)

            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo),
                "--ticket-id", "T-1",
                "--decision", "APPROVED",
                "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

            approval = json.loads((records / "pr-approval-record.json").read_text(encoding="utf-8"))
            self.assertEqual(approval["decision"], "APPROVED")
            self.assertEqual(approval["pr_head_sha"], head)

            manifest = json.loads((records / "delivery-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "HUMAN_APPROVED")

    def test_rejected_decision_sets_state_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = _init_repo_with_delivery_branch(repo)
            records = repo / "sdlc-records" / "T-1"
            _write_manifest(records, pr_head_sha=head)

            subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "REJECTED", "--approved-by", "tester",
            ], check=True, capture_output=True, text=True)

            manifest = json.loads((records / "delivery-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "REJECTED")

    def test_branch_moved_since_review_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = _init_repo_with_delivery_branch(repo)
            records = repo / "sdlc-records" / "T-1"
            _write_manifest(records, pr_head_sha=head)

            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            _git(["add", "app.py"], repo)
            _git(["commit", "-q", "-m", "more work after review"], repo)

            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "APPROVED", "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("changed since", result.stderr + result.stdout)

    def test_wrong_state_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = _init_repo_with_delivery_branch(repo)
            records = repo / "sdlc-records" / "T-1"
            _write_manifest(records, pr_head_sha=head, state="AGENT_REVIEWING")

            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "APPROVED", "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AWAITING_HUMAN_REVIEW", result.stderr + result.stdout)

    def test_immutable_redecision_for_same_head_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = _init_repo_with_delivery_branch(repo)
            records = repo / "sdlc-records" / "T-1"
            _write_manifest(records, pr_head_sha=head)

            subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "APPROVED", "--approved-by", "tester",
            ], check=True, capture_output=True, text=True)

            # Re-point the manifest back to AWAITING_HUMAN_REVIEW at the same
            # HEAD to simulate a second attempt at deciding the same PR HEAD.
            _write_manifest(records, pr_head_sha=head)
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "APPROVED", "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("immutable", result.stderr + result.stdout)

    def test_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            _init_repo_with_delivery_branch(repo)
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo), "--ticket-id", "T-1",
                "--decision", "APPROVED", "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
