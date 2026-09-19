#!/usr/bin/env bash
set -euo pipefail

BASE=".agentic-sdlc/cao/profiles"

cao profile validate "$BASE/context-normalizer.md"
cao profile validate "$BASE/planning-analyst.md"
cao profile validate "$BASE/plan-author.md"
cao profile validate "$BASE/plan-reviewer.md"

cao install "$BASE/context-normalizer.md"
cao install "$BASE/planning-analyst.md"
cao install "$BASE/plan-author.md"
cao install "$BASE/plan-reviewer.md"

cao profile show sdlc_context_normalizer
cao profile show sdlc_planning_analyst
cao profile show sdlc_plan_author
cao profile show sdlc_plan_reviewer
