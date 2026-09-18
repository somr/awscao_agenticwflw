#!/usr/bin/env bash
set -euo pipefail
# A separate installer; never replaces Planning or Delivery workflows/profiles.
REPO_ROOT="${1:-$(pwd)}"
WORKFLOW_DIR="${CAO_WORKFLOW_DIR:-$HOME/.aws/cli-agent-orchestrator/workflows}"
SOURCE="$REPO_ROOT/.agentic-sdlc/cao/workflows/source_review.py"
TARGET="$WORKFLOW_DIR/source_review.py"
python3 - "$SOURCE" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
compile(path.read_text(), str(path), 'exec')
PY
cao workflow list >/dev/null
mkdir -p "$WORKFLOW_DIR"
if [[ -e "$TARGET" || -e "$WORKFLOW_DIR/source_review.yaml" ]]; then
  echo "Refusing to overwrite an installed source_review workflow. Stop its runs and archive its spec before upgrading." >&2
  exit 1
fi
STAGED=$(mktemp "$WORKFLOW_DIR/source_review_candidate_XXXXXXXX.py")
trap 'rm -f "$STAGED"' EXIT
cp "$SOURCE" "$STAGED"
cao workflow validate "$STAGED"
for role in mapper correctness security validator feedback; do
  cao profile validate "$REPO_ROOT/.agentic-sdlc/cao/profiles/source-$role.md"
done
for role in mapper correctness security validator feedback; do
  if cao profile show "sdlc_source_$role" >/dev/null 2>&1; then
    echo "Profile sdlc_source_$role already exists; refusing to replace it." >&2
    exit 1
  fi
done
for role in mapper correctness security validator feedback; do
  cao install "$REPO_ROOT/.agentic-sdlc/cao/profiles/source-$role.md"
done
# Hard-link creation fails atomically if another installer created TARGET.
ln "$STAGED" "$TARGET"
cao workflow validate "$TARGET"
echo "Installed Workflow 3: $TARGET"
