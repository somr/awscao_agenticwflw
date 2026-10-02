"""Git mechanics for parallel hybrid workers: one worktree per task, merged in task order.

A worker that runs in a worktree is confined to it by the same write-scope hook,
because the hook resolves every root against the worker's working directory
(see agentic-sdlc-docs/verification/parallel-workers-spike.md). That only holds
while the worktree's copy of the hook, its settings and the registry equal the
main checkout's, so check_trusted_files runs before any worker starts.
Only Python runs Git; workers never do.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import WorkflowContractError
from .artifacts import _sha256_file

TRUSTED_FILES = (
    ".claude/settings.json",
    ".claude/hooks/restrict-write-scope.py",
    ".agentic-sdlc/cao/specialists.json",
    "agentic-sdlc-project.json",
)


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise WorkflowContractError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _existing(tree: Path, roots: list[str]) -> list[str]:
    # git add fails on a pathspec that matches nothing, and a new module may not exist yet.
    return [root for root in roots if (tree / root).exists()]


def add_worktree(repo: Path, path: Path, branch: str) -> Path:
    """Create a worktree on a new scratch branch at the main checkout's HEAD."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", branch, str(path), "HEAD"], cwd=repo)
    return path


def check_trusted_files(repo: Path, worktree: Path) -> None:
    """Stop before any worker starts if the worktree's boundary files differ from the main checkout's."""
    for relative in TRUSTED_FILES:
        main, copy = repo / relative, worktree / relative
        if main.is_file() != copy.is_file() or (main.is_file() and _sha256_file(main) != _sha256_file(copy)):
            raise WorkflowContractError(
                f"{relative} in worktree {worktree.name} differs from the main checkout; "
                "commit or discard local changes to the write boundary before running parallel workers"
            )


def changed_paths(tree: Path, roots: list[str]) -> list[str]:
    """Paths changed under the source roots (modified, added, deleted, untracked; both sides of a rename)."""
    existing = _existing(tree, roots)
    if not existing:
        return []
    output = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--", *existing], cwd=tree)
    entries = output.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC":  # -z puts a rename's original path in the next entry
            paths.add(entries[index])
            index += 1
    return sorted(paths)


def outside_owned(paths: list[str], owns: list[str]) -> list[str]:
    return [path for path in paths if not any(path == own or path.startswith(own + "/") for own in owns)]


def commit_task(tree: Path, roots: list[str], message: str) -> str | None:
    """Commit the tree's source-root changes; None when the task changed nothing."""
    if not changed_paths(tree, roots):
        return None
    _git(["add", "--", *_existing(tree, roots)], cwd=tree)
    _git(["commit", "-q", "-m", message], cwd=tree)
    return head(tree)


def head(tree: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=tree).strip()


def task_patch(tree: Path, base: str) -> str:
    return _git(["diff", base, "HEAD"], cwd=tree)


def cherry_pick(repo: Path, sha: str) -> bool:
    """Apply one task commit to the main checkout; on conflict abort and report False."""
    result = subprocess.run(["git", "cherry-pick", sha], cwd=str(repo), capture_output=True, text=True)
    if result.returncode == 0:
        return True
    abort = subprocess.run(["git", "cherry-pick", "--abort"], cwd=str(repo), capture_output=True, text=True)
    if abort.returncode != 0:
        raise WorkflowContractError(f"git cherry-pick --abort failed after a conflict on {sha}: {abort.stderr.strip()}")
    return False


def remove_worktree(repo: Path, path: Path, branch: str) -> list[str]:
    """Best-effort cleanup; returns warnings instead of raising so it can run in a finally block."""
    warnings = []
    for args in (["worktree", "remove", "--force", str(path)], ["branch", "-D", branch]):
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
        if result.returncode != 0:
            warnings.append(f"git {' '.join(args)}: {result.stderr.strip()}")
    return warnings


def prune_worktrees(repo: Path) -> None:
    _git(["worktree", "prune"], cwd=repo)
