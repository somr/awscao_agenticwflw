#!/usr/bin/env python3
"""Record a human decision for a reviewed Development Plan."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Durable records live outside .agentic-sdlc/. Keep in sync with sdlc_workflows/artifacts.py.
RECORDS_DIR = "sdlc-records"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve or reject an agentic SDLC Development Plan")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--decision", choices=("APPROVED", "REJECTED"), required=True)
    parser.add_argument("--approved-by", default=getpass.getuser())
    parser.add_argument("--reference", default="")
    args = parser.parse_args()

    repo = Path(args.repository_root).resolve()
    records = repo / RECORDS_DIR / args.ticket_id
    plan = records / "development-plan.md"
    manifest_path = records / "execution-manifest.json"
    approval_path = records / "plan-approval-record.json"
    if not plan.is_file() or not manifest_path.is_file():
        raise SystemExit("Reviewed plan or execution manifest is missing")

    manifest = read_json(manifest_path)
    actual_sha = sha256_file(plan)
    expected_sha = manifest.get("plan_sha256")
    if actual_sha != expected_sha:
        raise SystemExit(
            "Plan hash does not match the reviewed execution manifest. "
            "Do not approve a modified plan; run Planning Workflow 1 again."
        )

    if approval_path.exists():
        previous = read_json(approval_path)
        if previous.get("plan_sha256") == actual_sha:
            raise SystemExit(
                f"This exact plan already has decision {previous.get('decision')!r}; "
                "approval decisions are immutable."
            )
        history = records / "approval-history" / f"{previous.get('plan_sha256', 'unknown')}.json"
        write_json(history, previous)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    approval = {
        "schema_version": "1.0",
        "ticket_id": args.ticket_id,
        "plan_path": str(plan.relative_to(repo)),
        "plan_sha256": actual_sha,
        "repository_baseline_sha": manifest["repository_baseline_sha"],
        "decision": args.decision,
        "approved_by": args.approved_by,
        "approved_at": now,
        "approval_reference": args.reference or None,
    }
    write_json(approval_path, approval)

    manifest["state"] = args.decision
    manifest["approval_record"] = str(approval_path.relative_to(repo))
    manifest["approval_decided_at"] = now
    write_json(manifest_path, manifest)

    print(json.dumps({
        "ticket_id": args.ticket_id,
        "decision": args.decision,
        "plan_sha256": actual_sha,
        "approval_record": str(approval_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
