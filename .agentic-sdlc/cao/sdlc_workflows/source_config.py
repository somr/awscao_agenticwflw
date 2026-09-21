"""Source roots: the directories where Delivery agents may write, commit and verify generated source.

The configuration lives in the trusted registry (.agentic-sdlc/cao/specialists.json), which
agents cannot edit. .claude/hooks/restrict-write-scope.py keeps a standalone copy of
validate_source_config(); tests/test_source_config.py runs both over one corpus, so any rule
added here must be added there.
"""
from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any

from .errors import WorkflowContractError
from .artifacts import _read_json

REGISTRY_RELATIVE_PATH = ".agentic-sdlc/cao/specialists.json"
DEFAULT_SOURCE_ROOTS = ["app"]
# Profile -> None means "all source roots". Absent config keeps the original behaviour.
DEFAULT_WRITE_PROFILES: dict[str, list[str] | None] = {"sdlc_implementer": None, "sdlc_remediator": None}
MAX_SOURCE_ROOTS = 16
# The SDLC's own folders are never generated source.
FORBIDDEN_ROOTS = (
    ".git",
    ".claude",
    ".agentic-sdlc",
    "agentic-sdlc-records",
    "agentic-sdlc-docs",
    "agentic-sdlc-local-inputs",
)
# A typo in the config must not give a read-only role write access.
READ_ONLY_PROFILES = frozenset({
    "sdlc_code_supervisor",
    "sdlc_pr_reviewer",
    "sdlc_context_normalizer",
    "sdlc_planning_analyst",
    "sdlc_plan_author",
    "sdlc_plan_reviewer",
})
READ_ONLY_PROFILE_PREFIX = "sdlc_source_"


def _overlaps_forbidden(root: str) -> bool:
    return any(
        root == forbidden or root.startswith(forbidden + "/") or forbidden.startswith(root + "/")
        for forbidden in FORBIDDEN_ROOTS
    )


def _normalize_root(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowContractError(f"{label} must be a non-empty string")
    # ':' blocks git pathspec magic (":(top)...", ":!x"); glob characters and control characters are never valid.
    if any(ord(char) < 32 or char in "\\*?[]:" for char in value):
        raise WorkflowContractError(f"{label} contains a character that is not allowed: {value!r}")
    parts = value.split("/")
    if value.startswith("/") or value == "." or ".." in parts or value != posixpath.normpath(value):
        raise WorkflowContractError(f"{label} must be a normalized relative path inside the repository: {value!r}")
    if _overlaps_forbidden(value):
        raise WorkflowContractError(f"{label} overlaps a folder that is not generated source: {value!r}")
    return value


def _inside_any(root: str, roots: list[str]) -> bool:
    return any(root == candidate or root.startswith(candidate + "/") for candidate in roots)


def validate_source_config(raw: Any) -> dict[str, Any]:
    """Validate the registry's source-root keys and return them with defaults applied.

    Returns {"source_roots": [...], "write_profiles": {profile: [roots]}}. A profile mapped to
    None in the config gets every source root. Missing keys use the defaults; a present but
    invalid value is an error, never a fallback to the defaults.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise WorkflowContractError("registry must be a JSON object")
    roots_value = raw.get("source_roots", DEFAULT_SOURCE_ROOTS)
    if not isinstance(roots_value, list) or not 1 <= len(roots_value) <= MAX_SOURCE_ROOTS:
        raise WorkflowContractError(f"source_roots must be a list of 1 to {MAX_SOURCE_ROOTS} directories")
    roots: list[str] = []
    for index, item in enumerate(roots_value):
        root = _normalize_root(item, f"source_roots[{index}]")
        if root in roots:
            raise WorkflowContractError(f"source_roots lists {root!r} more than once")
        roots.append(root)

    profiles_value = raw.get("write_profiles", DEFAULT_WRITE_PROFILES)
    if not isinstance(profiles_value, dict) or not profiles_value:
        raise WorkflowContractError("write_profiles must name at least one profile")
    write_profiles: dict[str, list[str]] = {}
    for name, value in profiles_value.items():
        if not isinstance(name, str) or not name:
            raise WorkflowContractError("write_profiles names must be non-empty strings")
        if name in READ_ONLY_PROFILES or name.startswith(READ_ONLY_PROFILE_PREFIX):
            raise WorkflowContractError(f"write_profiles may not list the read-only profile {name!r}")
        if value is None:
            write_profiles[name] = list(roots)
            continue
        if not isinstance(value, list) or not value:
            raise WorkflowContractError(f"write_profiles[{name!r}] must be null or a non-empty list of roots")
        subset: list[str] = []
        for index, item in enumerate(value):
            root = _normalize_root(item, f"write_profiles[{name!r}][{index}]")
            if not _inside_any(root, roots):
                raise WorkflowContractError(f"write_profiles[{name!r}] lists {root!r}, which is not inside a source root")
            if root in subset:
                raise WorkflowContractError(f"write_profiles[{name!r}] lists {root!r} more than once")
            subset.append(root)
        write_profiles[name] = subset
    return {"source_roots": roots, "write_profiles": write_profiles}


def load_source_config(repo: Path) -> dict[str, Any]:
    """Read and validate the source-root config; a missing registry file means the defaults."""
    path = repo / REGISTRY_RELATIVE_PATH
    if not path.exists():
        return validate_source_config({})
    try:
        raw = _read_json(path)
    except (OSError, ValueError) as exc:
        raise WorkflowContractError(f"cannot read {REGISTRY_RELATIVE_PATH}: {exc}") from exc
    return validate_source_config(raw)


def resolve_source_roots(repo: Path, roots: list[str]) -> list[Path]:
    """Resolve roots against the repository and refuse any that leave it or land in an SDLC folder.

    Symlinks are followed, so a root that is a symlink to elsewhere (or into .git) is rejected.
    Roots that do not exist yet are fine: a worker may create a new module.
    """
    base = repo.resolve()
    resolved: list[Path] = []
    for root in roots:
        path = (base / root).resolve()
        if path == base or not path.is_relative_to(base):
            raise WorkflowContractError(f"source root {root!r} resolves outside the repository")
        if _overlaps_forbidden(path.relative_to(base).as_posix()):
            raise WorkflowContractError(f"source root {root!r} resolves into a folder that is not generated source")
        resolved.append(path)
    return resolved
