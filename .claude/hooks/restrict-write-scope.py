#!/usr/bin/env python3
"""PreToolUse hook: restrict CAO worker writes to .agentic-sdlc/runtime/**.

SECURITY DESIGN — full writeup in .agentic-sdlc/cao/workflows/README.md under
"Answer file delivery & the write-scope hook". Summary: the CAO planning-
workflow agent profiles (sdlc_context_normalizer, sdlc_planning_analyst,
sdlc_plan_author, sdlc_plan_reviewer) are granted fs_write (Claude Code's
Write/Edit/NotebookEdit tools) so each step can deliver its answer as a file
instead of via unreliable terminal-text parsing (see dev_plan.py). Those
profiles process untrusted external content (Jira/Confluence text), so a
prompt-injection payload hidden in that content could try to write or
overwrite an arbitrary file.

Neither CAO's own allowedTools grant nor Claude Code's permissions.allow/deny
rules stop that: CAO's tool-category grant has no path scoping, and CAO
always launches these workers with --dangerously-skip-permissions, which
bypasses Claude Code's own permission-rule layer entirely. A PreToolUse hook
is not bypassed by that flag, which is why this hook — not a settings.json
permission rule — is the actual enforcement mechanism.

Scope: this hook only restricts CAO-spawned worker terminals, identified by
the CAO_TERMINAL_ID environment variable CAO sets via tmux pane env
inheritance for every terminal it launches. A session without that variable
(the human's own interactive Claude Code session, or any other ad-hoc use of
this repository) is left unrestricted by this hook.

Do not narrow or remove this hook without keeping an equivalent restriction
in place; do not rely on permissions.allow/deny as a substitute.
"""
import json
import os
import sys

ALLOWED_RELATIVE_ROOT = ".agentic-sdlc/runtime"


def main() -> int:
    if "CAO_TERMINAL_ID" not in os.environ:
        # Not a CAO-spawned worker terminal; nothing to restrict here.
        return 0

    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not path:
        return 0

    cwd = os.getcwd()
    allowed_root = os.path.realpath(os.path.join(cwd, ALLOWED_RELATIVE_ROOT))
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))

    if target == allowed_root or target.startswith(allowed_root + os.sep):
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"CAO worker writes are restricted to {ALLOWED_RELATIVE_ROOT}/** "
                "in this project. See .agentic-sdlc/cao/workflows/README.md, "
                "'Answer file delivery & the write-scope hook'."
            ),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
