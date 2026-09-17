#!/usr/bin/env bash
set -euo pipefail

# Install Delivery Workflow 2 into CAO's validated workflow directory.
#
# Near-identical to install.sh (Workflow 1's installer) with only the
# source/target/staged/collision paths changed — kept as a separate script
# rather than parameterizing install.sh, so Workflow 1's already-working
# install path is never at risk from Workflow 2 changes.
#
# CAO workflow authoring/validation commands are HTTP clients to cao-server.
# The server deliberately refuses to validate an arbitrary repository path,
# so the canonical repository copy is first staged INSIDE the CAO workflow
# directory and only then validated.

REPO_ROOT="${1:-$(pwd)}"
SOURCE="$REPO_ROOT/.agentic-sdlc/cao/workflows/deliver.py"
WORKFLOW_DIR="${CAO_WORKFLOW_DIR:-$HOME/.aws/cli-agent-orchestrator/workflows}"
TARGET="$WORKFLOW_DIR/deliver.py"
STAGED="$WORKFLOW_DIR/deliver_candidate.py"
COLLISION="$WORKFLOW_DIR/deliver.yaml"

if [[ ! -f "$SOURCE" ]]; then
  echo "Workflow source not found: $SOURCE" >&2
  exit 1
fi

# Cheap local syntax gate. This does not replace CAO validation.
python3 - "$SOURCE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
compile(p.read_text(encoding="utf-8"), str(p), "exec")
print(f"Python syntax OK: {p}")
PY

# CAO workflow CLI commands are server-backed. Fail with a useful message if
# the server is not running instead of failing later during validate.
if ! cao workflow list >/dev/null 2>&1; then
  cat >&2 <<'MSG'
Cannot reach a running cao-server.

Start it in another terminal:

    cao-server

Then re-run this installer.
MSG
  exit 1
fi

mkdir -p "$WORKFLOW_DIR"

if [[ -e "$COLLISION" ]]; then
  echo "Refusing install: $COLLISION collides with Python workflow deliver.py" >&2
  exit 1
fi

# The server only validates workflow specs inside its permitted workflow
# directory. Stage there, validate the staged bytes, then atomically promote.
rm -f "$STAGED"
cp "$SOURCE" "$STAGED"
trap 'rm -f "$STAGED"' EXIT

printf 'Validating staged workflow with CAO: %s\n' "$STAGED"
cao workflow validate "$STAGED"

# Preserve a simple rollback copy when replacing an existing installation.
if [[ -f "$TARGET" ]]; then
  cp "$TARGET" "$TARGET.bak"
fi

mv "$STAGED" "$TARGET"
trap - EXIT

# Validate the exact final path as a last check.
printf 'Validating installed workflow with CAO: %s\n' "$TARGET"
cao workflow validate "$TARGET"

printf 'Installed Delivery Workflow 2 at: %s\n' "$TARGET"
if [[ -f "$TARGET.bak" ]]; then
  printf 'Previous installation backed up at: %s\n' "$TARGET.bak"
fi
