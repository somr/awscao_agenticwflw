#!/usr/bin/env python3
"""PreToolUse hook: restrict CAO worker writes to their allowed root(s).

SECURITY DESIGN — full writeup in docs/workflows/planning.md under
"Answer file delivery & the write-scope hook". Summary: CAO agent profiles
are granted fs_write (Claude Code's Write/Edit/NotebookEdit tools) so each
step can deliver its answer as a file instead of via unreliable
terminal-text parsing (see dev_plan.py). Workflow 1's planning profiles
process untrusted external content (Jira/Confluence text), so a
prompt-injection payload hidden in that content could try to write or
overwrite an arbitrary file. Workflow 2's sdlc_implementer/sdlc_remediator
profiles additionally need to write real application source under app/**.

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
under the roots named for its agent profile in WIDENED_WRITE_ROOTS, but only
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

Do not narrow or remove this hook without keeping an equivalent restriction
in place; do not rely on permissions.allow/deny as a substitute.
"""
import json
import os
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
    "sdlc-records",  # durable workflow records; outside the embeddable .agentic-sdlc/
]

# Additional roots a given agent_profile may write under, on top of
# ALWAYS_ALLOWED_ROOT. Only granted after a live, server-confirmed profile
# match — see module docstring.
WIDENED_WRITE_ROOTS: dict[str, list[str]] = {
    "sdlc_implementer": ["app"],
    "sdlc_remediator": ["app"],
}

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
    widened_roots = WIDENED_WRITE_ROOTS.get(profile, []) if profile else []
    for widened_root in widened_roots:
        if _is_under(target, _resolve(cwd, widened_root)):
            return 0

    return _deny(
        f"CAO worker writes are restricted to {ALWAYS_ALLOWED_ROOT}/**"
        + (f" (plus {', '.join(widened_roots)}/** for profile {profile!r})" if widened_roots else "")
        + " in this project. See docs/workflows/planning.md, "
        "'Answer file delivery & the write-scope hook'."
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
