#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${1:-$(pwd)}"
exec python3 "$REPO_ROOT/.agentic-sdlc/cao/install_workflow.py" source_remediate --repository-root "$REPO_ROOT"
