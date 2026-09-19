"""Artifact persistence and content digests; no workflow policy."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any

# Durable workflow records live in the host project, outside the embeddable
# .agentic-sdlc/ tooling directory, so upgrading the tooling cannot touch them.
# Keep in sync with scripts/approve_plan.py, scripts/record_pr_approval.py and
# the write-scope hook (.claude/hooks/restrict-write-scope.py).
RECORDS_DIR = "sdlc-records"

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value.rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())
