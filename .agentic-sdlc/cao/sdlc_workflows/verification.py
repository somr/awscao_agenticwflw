"""Trusted verification command execution shared by development workflows."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from .artifacts import _write_json, _write_text

VERIFICATION_TIMEOUT_SECONDS = 300

def _run_verification(repo: Path, evidence_dir: Path, label: str, commands: list[list[str]]) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    command_results: list[dict[str, Any]] = []
    all_passed = True
    for index, command in enumerate(commands, start=1):
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=VERIFICATION_TIMEOUT_SECONDS,
            )
            returncode: int | None = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout = exc.stdout or ""
            stderr = f"{exc.stderr or ''}\n[timed out after {VERIFICATION_TIMEOUT_SECONDS}s]"
        except OSError as exc:
            returncode = None
            stdout, stderr = "", str(exc)
        passed = returncode == 0
        all_passed = all_passed and passed
        command_results.append({"command": command, "returncode": returncode, "passed": passed})
        _write_text(
            evidence_dir / f"{label}-cmd{index}.log",
            f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        )
    summary = {"passed": all_passed, "commands": command_results}
    _write_json(evidence_dir / f"{label}.json", summary)
    return summary
