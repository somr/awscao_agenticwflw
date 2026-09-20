from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agentic-sdlc" / "scripts" / "approve_plan.py"


class ApprovalHelperTest(unittest.TestCase):
    def test_approval_binds_to_exact_plan_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            records = repo / "agentic-sdlc-records" / "T-1"
            records.mkdir(parents=True)
            plan = records / "development-plan.md"
            plan.write_text("# Plan\n", encoding="utf-8")
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            (records / "execution-manifest.json").write_text(json.dumps({
                "plan_sha256": digest,
                "repository_baseline_sha": "abc123",
                "state": "AWAITING_HUMAN_APPROVAL"
            }), encoding="utf-8")
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo),
                "--ticket-id", "T-1",
                "--decision", "APPROVED",
                "--approved-by", "tester",
            ], check=True, capture_output=True, text=True)
            approval = json.loads((records / "plan-approval-record.json").read_text(encoding="utf-8"))
            self.assertEqual(approval["decision"], "APPROVED")
            self.assertEqual(approval["plan_sha256"], digest)

    def test_modified_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            records = repo / "agentic-sdlc-records" / "T-1"
            records.mkdir(parents=True)
            plan = records / "development-plan.md"
            plan.write_text("# Original\n", encoding="utf-8")
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            (records / "execution-manifest.json").write_text(json.dumps({
                "plan_sha256": digest,
                "repository_baseline_sha": "abc123",
                "state": "AWAITING_HUMAN_APPROVAL"
            }), encoding="utf-8")
            plan.write_text("# Modified\n", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--repository-root", str(repo),
                "--ticket-id", "T-1",
                "--decision", "APPROVED",
                "--approved-by", "tester",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Plan hash does not match", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
