"""Resume a BLOCKED Delivery run from its manifest and the commits on the delivery branch.

The manifest in agentic-sdlc-records/ (which agents cannot write) and Git are the only
inputs: a container run loses .agentic-sdlc/runtime/, so nothing is read from there.
Commits a human adds after the recorded branch head are accepted and listed; the
recorded commits must still be in the branch, so a rebase cannot be resumed.
See agentic-sdlc-docs/plans/delivery-resume.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .artifacts import _read_json
from .source_config import _inside_any
from .worktrees import _git

RESUMABLE_SCHEMA_VERSIONS = ("1.1",)
RESUME_POINTS = ("implementation", "verification")


def load_resumable_manifest(path: Path, *, ticket_id: str, plan_sha256: str) -> dict[str, Any]:
    """The earlier run's manifest, when it describes a BLOCKED delivery that can be resumed."""
    if not path.is_file():
        raise WorkflowContractError(f"resume needs the earlier run's {path}; none found")
    previous = _read_json(path)
    if not isinstance(previous, dict) or previous.get("schema_version") not in RESUMABLE_SCHEMA_VERSIONS:
        raise WorkflowContractError(
            f"{path.name} has schema_version {previous.get('schema_version') if isinstance(previous, dict) else None!r}; "
            f"only {', '.join(RESUMABLE_SCHEMA_VERSIONS)} records enough to resume"
        )
    if previous.get("state") != "BLOCKED":
        raise WorkflowContractError(f"only a BLOCKED delivery can be resumed; {path.name} says {previous.get('state')!r}")
    if previous.get("resume_point") not in RESUME_POINTS:
        raise WorkflowContractError(f"{path.name} has no valid resume_point")
    if previous.get("ticket_id") != ticket_id:
        raise WorkflowContractError(f"{path.name} belongs to ticket {previous.get('ticket_id')!r}, not {ticket_id!r}")
    if previous.get("plan_sha256") != plan_sha256:
        raise WorkflowContractError("the approved plan changed since the BLOCKED run; plan changes need a new delivery")
    for key in ("branch_head_sha", "base_sha", "implementation_mode"):
        if not isinstance(previous.get(key), str) or not previous[key]:
            raise WorkflowContractError(f"{path.name} lacks {key}")
    return previous


def _is_ancestor(repo: Path, sha: str, ref: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", sha, ref], cwd=str(repo), capture_output=True)
    return result.returncode == 0


def _names(output: str) -> list[str]:
    return [line for line in output.split("\n") if line]


def _commit_paths(repo: Path, sha: str) -> list[str]:
    """Paths a commit changed against its first parent (for a merge: what it brought into the branch)."""
    parents = _git(["rev-list", "--parents", "-n", "1", sha], cwd=repo).split()[1:]
    if not parents:
        return sorted(_names(_git(["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha], cwd=repo)))
    return sorted(_names(_git(["diff", "--name-only", parents[0], sha], cwd=repo)))


def check_branch(repo: Path, previous: dict[str, Any], roots: list[str]) -> None:
    """The checked-out delivery branch still holds the earlier run's work, unchanged."""
    if _git(["status", "--porcelain", "--untracked-files=no"], cwd=repo).strip():
        raise WorkflowContractError("resume needs every change committed: commit or discard the uncommitted edits first")
    for entry in [{"sha": previous["branch_head_sha"], "step": "branch head"}] + previous.get("delivery_commits", []):
        if not _is_ancestor(repo, entry["sha"], "HEAD"):
            raise WorkflowContractError(
                f"recorded commit {entry['sha'][:12]} ({entry['step']}) is no longer in the delivery branch; "
                "add new commits on top (a merge is fine), do not rebase or reset"
            )
    for entry in previous.get("delivery_commits", []):
        outside = [path for path in _commit_paths(repo, entry["sha"]) if not _inside_any(path, roots)]
        if outside:
            raise WorkflowContractError(
                f"recorded commit {entry['sha'][:12]} ({entry['step']}) changed {', '.join(outside)}, "
                "outside the current source roots; restore the roots or start a new delivery"
            )


def hand_commits(repo: Path, since: str) -> list[dict[str, Any]]:
    """Commits added after the recorded branch head, oldest first, with the paths each changed.

    Follows first parents only: merging the base branch shows as one merge commit, not as
    every base commit it brought in.
    """
    shas = _git(["rev-list", "--reverse", "--first-parent", f"{since}..HEAD"], cwd=repo).split()
    return [{"sha": sha, "subject": _git(["log", "-1", "--format=%s", sha], cwd=repo).strip(),
             "paths": _commit_paths(repo, sha)} for sha in shas]


def base_drift(repo: Path, base_branch: str, base_sha_at_start: str) -> dict[str, Any]:
    """How far the base branch moved past the delivery branch, and which files both changed."""
    fork = _git(["merge-base", "HEAD", base_branch], cwd=repo).strip()
    behind = int(_git(["rev-list", "--count", f"HEAD..{base_branch}"], cwd=repo).strip())
    overlapping: list[str] = []
    if behind:
        base_paths = set(_names(_git(["diff", "--name-only", fork, base_branch], cwd=repo)))
        delivery_paths = set(_names(_git(["diff", "--name-only", fork, "HEAD"], cwd=repo)))
        overlapping = sorted(base_paths & delivery_paths)
    return {
        "base_sha_at_start": base_sha_at_start,
        "base_sha_now": _git(["rev-parse", base_branch], cwd=repo).strip(),
        "commits_behind": behind,
        "overlapping_paths": overlapping,
    }
