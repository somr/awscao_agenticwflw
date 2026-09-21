#!/usr/bin/env python3
"""PreToolUse hook: restrict CAO worker writes to their allowed root(s).

SECURITY DESIGN — full writeup in agentic-sdlc-docs/reference/write-scope-hook.md.
Summary: CAO agent profiles
are granted fs_write (Claude Code's Write/Edit/NotebookEdit tools) so each
step can deliver its answer as a file instead of via unreliable
terminal-text parsing (see dev_plan.py). Workflow 1's planning profiles
process untrusted external content (Jira/Confluence text), so a
prompt-injection payload hidden in that content could try to write or
overwrite an arbitrary file. Workflow 2's sdlc_implementer/sdlc_remediator
profiles additionally need to write real application source under the project's
configured source roots (default app/**).

Neither CAO's own allowedTools grant nor Claude Code's permissions.allow/deny
rules stop any of this: CAO's tool-category grant has no path scoping, and
CAO always launches these workers with --dangerously-skip-permissions, which
bypasses Claude Code's own permission-rule layer entirely. A PreToolUse hook
is not bypassed by that flag, which is why this hook — not a settings.json
permission rule — is the actual enforcement mechanism.

Scope: this hook only restricts CAO-spawned worker terminals, identified by
the CAO_TERMINAL_ID environment variable CAO sets via tmux pane env
inheritance for every terminal it launches. A session without that variable
(the human's own interactive Claude Code session, or any other ad-hoc use of
this repository) is left unrestricted by this hook.

Role-aware widening (added for Workflow 2): every CAO-spawned terminal may
always write under ALWAYS_ALLOWED_ROOT. A terminal may additionally write
under the source roots the trusted registry names for its agent profile
(write_profiles in .agentic-sdlc/cao/specialists.json), but only
after this hook independently confirms that profile identity by querying
CAO's own terminal metadata (GET /terminals/{id} -> agent_profile) — never by
trusting anything the terminal's own environment claims about itself. That
field is set once at terminal creation and has no update endpoint (CAO
treats agent_profile/allowed_tools as creation-only), so it cannot be
spoofed by anything running inside the terminal, including a prompt-injected
agent. Any failure to confirm the profile (network error, timeout, missing
field, unrecognized value) fails CLOSED: no widened root is granted, which is
exactly today's pre-Workflow-2 behavior. DENY_ALWAYS_ROOTS is checked before
any widened root and always wins, so a widened profile still cannot rewrite
its own guardrails (this hook, its wiring, or already-published governance
records).

Configurable source roots: the roots come from the registry keys source_roots and
write_profiles, whose defaults ("app"; implementer and remediator) equal the original
hardcoded behaviour, so a project without those keys is unchanged. The registry sits
under a DENY_ALWAYS_ROOT, so no worker can edit it. The configuration is validated on
every decision by validate_source_config() below, a standalone copy of the workflow's
validator that tests/test_source_config.py keeps in step. A registry that is present
but unreadable, malformed or invalid grants NO widening to ANY profile and never falls
back to the defaults: a typo must not widen access. Every root's real path must stay
inside the repository and outside the SDLC's own folders, so a symlinked root cannot be
used to write elsewhere. Read-only profiles (supervisor, reviewers, planning and
source-review roles) can never be listed. Roots that do not exist yet are allowed, so a
worker may create a new module.

Do not narrow or remove this hook without keeping an equivalent restriction
in place; do not rely on permissions.allow/deny as a substitute.
"""
import json
import os
import posixpath
import sys
import urllib.error
import urllib.request

ALWAYS_ALLOWED_ROOT = ".agentic-sdlc/runtime"

# Checked before any widened root; always wins, even for a widened profile.
DENY_ALWAYS_ROOTS = [
    ".git",
    ".claude",
    ".agentic-sdlc/cao",
    ".agentic-sdlc/policies",
    ".agentic-sdlc/contracts",
    ".agentic-sdlc/templates",
    ".agentic-sdlc/schemas",
    "agentic-sdlc-records",  # durable workflow records; outside the embeddable .agentic-sdlc/
]

# The additional roots a given agent_profile may write under, on top of
# ALWAYS_ALLOWED_ROOT, come from the trusted registry (see the source-root block
# below) and are only granted after a live, server-confirmed profile match.

# --- Source-root configuration -------------------------------------------------
# Standalone copy of validate_source_config() in
# .agentic-sdlc/cao/sdlc_workflows/source_config.py. This hook cannot import the
# workflow bundle, so tests/test_source_config.py runs both over one corpus (parity
# test). Any rule added there must be added here, and the other way round.
REGISTRY_RELATIVE_PATH = ".agentic-sdlc/cao/specialists.json"
DEFAULT_SOURCE_ROOTS = ["app"]
DEFAULT_WRITE_PROFILES = {"sdlc_implementer": None, "sdlc_remediator": None}
MAX_SOURCE_ROOTS = 16
FORBIDDEN_ROOTS = (
    ".git",
    ".claude",
    ".agentic-sdlc",
    "agentic-sdlc-records",
    "agentic-sdlc-docs",
    "agentic-sdlc-local-inputs",
)
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


def _normalize_root(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 or char in "\\*?[]:" for char in value):
        raise ValueError(f"{label} contains a character that is not allowed: {value!r}")
    parts = value.split("/")
    if value.startswith("/") or value == "." or ".." in parts or value != posixpath.normpath(value):
        raise ValueError(f"{label} must be a normalized relative path inside the repository: {value!r}")
    if _overlaps_forbidden(value):
        raise ValueError(f"{label} overlaps a folder that is not generated source: {value!r}")
    return value


def _inside_any(root: str, roots: list) -> bool:
    return any(root == candidate or root.startswith(candidate + "/") for candidate in roots)


def validate_source_config(raw) -> dict:
    """Same contract as source_config.validate_source_config(); raises ValueError."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("registry must be a JSON object")
    roots_value = raw.get("source_roots", DEFAULT_SOURCE_ROOTS)
    if not isinstance(roots_value, list) or not 1 <= len(roots_value) <= MAX_SOURCE_ROOTS:
        raise ValueError(f"source_roots must be a list of 1 to {MAX_SOURCE_ROOTS} directories")
    roots = []
    for index, item in enumerate(roots_value):
        root = _normalize_root(item, f"source_roots[{index}]")
        if root in roots:
            raise ValueError(f"source_roots lists {root!r} more than once")
        roots.append(root)

    profiles_value = raw.get("write_profiles", DEFAULT_WRITE_PROFILES)
    if not isinstance(profiles_value, dict) or not profiles_value:
        raise ValueError("write_profiles must name at least one profile")
    write_profiles = {}
    for name, value in profiles_value.items():
        if not isinstance(name, str) or not name:
            raise ValueError("write_profiles names must be non-empty strings")
        if name in READ_ONLY_PROFILES or name.startswith(READ_ONLY_PROFILE_PREFIX):
            raise ValueError(f"write_profiles may not list the read-only profile {name!r}")
        if value is None:
            write_profiles[name] = list(roots)
            continue
        if not isinstance(value, list) or not value:
            raise ValueError(f"write_profiles[{name!r}] must be null or a non-empty list of roots")
        subset = []
        for index, item in enumerate(value):
            root = _normalize_root(item, f"write_profiles[{name!r}][{index}]")
            if not _inside_any(root, roots):
                raise ValueError(f"write_profiles[{name!r}] lists {root!r}, which is not inside a source root")
            if root in subset:
                raise ValueError(f"write_profiles[{name!r}] lists {root!r} more than once")
            subset.append(root)
        write_profiles[name] = subset
    return {"source_roots": roots, "write_profiles": write_profiles}


def _load_source_config(cwd: str) -> dict:
    """Validated source-root config; a missing registry file means the defaults.

    Anything else that goes wrong (unreadable, malformed, invalid) raises: the caller
    must treat that as "no widening", never as "use the defaults".
    """
    try:
        with open(os.path.join(cwd, REGISTRY_RELATIVE_PATH), encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return validate_source_config({})
    return validate_source_config(raw)


def _resolve_roots(cwd: str, roots: list) -> None:
    """Reject any root whose real path leaves the repository or lands in an SDLC folder."""
    base = os.path.realpath(cwd)
    for root in roots:
        path = os.path.realpath(os.path.join(base, root))
        if path == base or not _is_under(path, base):
            raise ValueError(f"source root {root!r} resolves outside the repository")
        if _overlaps_forbidden(os.path.relpath(path, base).replace(os.sep, "/")):
            raise ValueError(f"source root {root!r} resolves into a folder that is not generated source")


HTTP_TIMEOUT_SECONDS = 2.0


def _cao_base_url() -> str:
    explicit = os.environ.get("CAO_API_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    host = os.environ.get("CAO_API_HOST", "127.0.0.1")
    port = os.environ.get("CAO_API_PORT", "9889")
    return f"http://{host}:{port}"


def _fetch_agent_profile(terminal_id: str) -> str | None:
    """Return the server-recorded agent_profile for this terminal, or None
    on any failure. None must always be treated as "no widened access" —
    never as "widen everything" or "use a cached/previous value"."""
    url = f"{_cao_base_url()}/terminals/{terminal_id}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    profile = payload.get("agent_profile")
    return profile if isinstance(profile, str) and profile else None


def _is_under(target: str, root: str) -> bool:
    return target == root or target.startswith(root + os.sep)


def _resolve(cwd: str, relative_root: str) -> str:
    return os.path.realpath(os.path.join(cwd, relative_root))


def main() -> int:
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not terminal_id:
        # Not a CAO-spawned worker terminal; nothing to restrict here.
        return 0

    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not path:
        return 0

    cwd = os.getcwd()
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))

    for deny_root in DENY_ALWAYS_ROOTS:
        if _is_under(target, _resolve(cwd, deny_root)):
            return _deny(f"writes to {deny_root}/** are never permitted for CAO workers")

    if _is_under(target, _resolve(cwd, ALWAYS_ALLOWED_ROOT)):
        return 0

    profile = _fetch_agent_profile(terminal_id)
    widened_roots: list = []
    config_problem = None
    if profile:
        try:
            config = _load_source_config(cwd)
            # A root that escapes the repository invalidates the whole config, for every profile.
            _resolve_roots(cwd, config["source_roots"])
            widened_roots = config["write_profiles"].get(profile, [])
        except (OSError, ValueError) as exc:
            config_problem = str(exc)
    for widened_root in widened_roots:
        if _is_under(target, _resolve(cwd, widened_root)):
            return 0

    if config_problem:
        return _deny(
            f"CAO worker writes are restricted to {ALWAYS_ALLOWED_ROOT}/** because the source-root "
            f"configuration in {REGISTRY_RELATIVE_PATH} is invalid ({config_problem}); no source root is "
            "writable until it is fixed. See agentic-sdlc-docs/reference/write-scope-hook.md."
        )
    return _deny(
        f"CAO worker writes are restricted to {ALWAYS_ALLOWED_ROOT}/**"
        + (f" (plus {', '.join(widened_roots)}/** for profile {profile!r})" if widened_roots else "")
        + " in this project. See agentic-sdlc-docs/reference/write-scope-hook.md."
    )


def _deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
