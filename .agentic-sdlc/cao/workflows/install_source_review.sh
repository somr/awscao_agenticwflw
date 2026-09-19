#!/usr/bin/env bash
set -euo pipefail
# Build all dependencies into a standalone artifact before CAO validation.
REPO_ROOT="${1:-$(pwd)}"
exec python3 "$REPO_ROOT/.agentic-sdlc/cao/install_workflow.py" source_review --repository-root "$REPO_ROOT"
