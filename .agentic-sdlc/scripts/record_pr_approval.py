#!/usr/bin/env python3
"""Record a human review decision for a delivered PR (Workflow 2 — DELIVER).

Mirrors approve_plan.py's shape and safety properties, keyed on the delivery
branch's current PR HEAD SHA instead of a file hash — a PR is a moving
target tied to its HEAD commit, not a static file, so "has this changed
since review" means "does the branch tip still match what was reviewed."
"""
from __future__ import annotations

import argparse
import getpass
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Durable records live outside .agentic-sdlc/. Keep in sync with sdlc_workflows/artifacts.py.
RECORDS_DIR = "agentic-sdlc-records"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def current_head_sha(repo: Path, branch: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch], cwd=str(repo), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(f"Could not resolve delivery branch {branch!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a human PR review decision for an agentic SDLC delivery")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--decision", choices=("APPROVED", "REJECTED"), required=True)
    parser.add_argument("--approved-by", default=getpass.getuser())
    parser.add_argument("--reference", default="")
    args = parser.parse_args()

    repo = Path(args.repository_root).resolve()
    records = repo / RECORDS_DIR / args.ticket_id
    manifest_path = records / "delivery-manifest.json"
    approval_path = records / "pr-approval-record.json"
    if not manifest_path.is_file():
        raise SystemExit("No delivery-manifest.json found; run the delivery workflow (deliver.py) first")

    manifest = read_json(manifest_path)
    if manifest.get("state") != "AWAITING_HUMAN_REVIEW":
        raise SystemExit(
            f"delivery-manifest.json state is {manifest.get('state')!r}, expected 'AWAITING_HUMAN_REVIEW'. "
            "Do not record a decision before the Human Review Brief has been produced."
        )

    delivery_branch = manifest.get("delivery_branch")
    if not delivery_branch:
        raise SystemExit("delivery-manifest.json is missing delivery_branch")

    actual_pr_head_sha = current_head_sha(repo, delivery_branch)
    expected_pr_head_sha = manifest.get("pr_head_sha")
    if actual_pr_head_sha != expected_pr_head_sha:
        raise SystemExit(
            "The delivery branch has changed since the Human Review Brief was produced "
            f"(reviewed PR HEAD {expected_pr_head_sha}, current {actual_pr_head_sha}). "
            "Do not approve a PR whose code changed after review. Record the decision on the reviewed HEAD, "
            "or start a new Delivery run from a fresh branch (see 'Human decisions' in the Delivery guide)."
        )

    if approval_path.exists():
        previous = read_json(approval_path)
        if previous.get("pr_head_sha") == actual_pr_head_sha:
            raise SystemExit(
                f"This exact PR HEAD already has decision {previous.get('decision')!r}; "
                "approval decisions are immutable."
            )
        history = records / "approval-history" / f"{previous.get('pr_head_sha', 'unknown')}.json"
        write_json(history, previous)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    approval = {
        "schema_version": "1.0",
        "ticket_id": args.ticket_id,
        "pr_reference": manifest.get("pr_reference"),
        "pr_head_sha": actual_pr_head_sha,
        "decision": args.decision,
        "approved_by": args.approved_by,
        "approved_at": now,
        "approval_reference": args.reference or None,
    }
    write_json(approval_path, approval)

    manifest["state"] = "HUMAN_APPROVED" if args.decision == "APPROVED" else "REJECTED"
    manifest["pr_approval_record"] = str(approval_path.relative_to(repo))
    manifest["pr_approval_decided_at"] = now
    write_json(manifest_path, manifest)

    print(json.dumps({
        "ticket_id": args.ticket_id,
        "decision": args.decision,
        "pr_head_sha": actual_pr_head_sha,
        "approval_record": str(approval_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
