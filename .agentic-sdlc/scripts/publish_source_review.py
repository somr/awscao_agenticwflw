#!/usr/bin/env python3
"""Explicitly publish an existing source-review artifact as a COMMENT review.

No publication occurs without --publish. No PR approval or source edits occur.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def gh(endpoint: str, *, payload: dict | None = None, paginate: bool = False):
    args = ["gh", "api", "--hostname", "github.com", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(f"GitHub request failed: {result.stderr[:1000]}")
    return json.loads(result.stdout)


def build_payload(root: Path) -> tuple[dict, dict, str]:
    report = json.loads((root / "code-review.json").read_text())
    if report.get("status") != "REVIEWED":
        raise ValueError("Only REVIEWED artifacts may be published; rerun stale/failed reviews")
    snapshot = report["snapshot"]
    repository, number = snapshot["repository"], snapshot["pr_number"]
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Publication requires a GitHub PR artifact")
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid PR number")
    if not re.fullmatch(r"[0-9a-f]{40}", snapshot["head_sha"]):
        raise ValueError("Invalid reviewed HEAD")
    comments = (root / "comments.md").read_text()
    if hashlib.sha256(comments.encode()).hexdigest() != report["comments_sha256"]:
        raise ValueError("comments.md differs from the reviewed artifact")
    marker = f"<!-- cao-source-review:{snapshot['base_sha']}:{snapshot['head_sha']} -->"
    body = comments + "\n" + marker
    if len(body.encode()) > 60000:
        raise ValueError("Review exceeds publication size limit; use the local artifact")
    return report, {"commit_id": snapshot["head_sha"], "event": "COMMENT", "body": body}, marker


def publish(root: Path) -> dict:
    report, payload, marker = build_payload(root)
    snapshot = report["snapshot"]
    endpoint = f"repos/{snapshot['repository']}/pulls/{snapshot['pr_number']}"
    # Exclusive local lock avoids double publication from concurrent invocations.
    # A crash leaves it behind deliberately: inspect GitHub before removing it.
    lock = root / "publication.lock"
    with lock.open("x"):
        pass
    try:
        metadata = gh(endpoint)
        if metadata["state"] != "open" or metadata["head"]["sha"] != snapshot["head_sha"] or metadata["base"]["sha"] != snapshot["base_sha"]:
            raise ValueError("PR base/HEAD changed or PR closed; start a fresh review")
        pages = gh(endpoint + "/reviews?per_page=100", paginate=True)
        existing = [review for page in pages for review in page
                    if marker in (review.get("body") or "") and review.get("commit_id") == snapshot["head_sha"]]
        receipt = existing[0] if existing else gh(endpoint + "/reviews", payload=payload)
        (root / "publication.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--publish", action="store_true", help="Post a COMMENT review to GitHub")
    args = parser.parse_args()
    root = args.run_directory.resolve()
    if args.publish:
        print(json.dumps(publish(root), indent=2))
    else:
        _, payload, _ = build_payload(root)
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
