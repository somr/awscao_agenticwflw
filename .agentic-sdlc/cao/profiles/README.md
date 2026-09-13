# CAO Planning Workflow Profiles

These are the canonical, version-controlled CAO profiles for Planning Workflow 1.

All four profiles currently use `claude_code` and are intentionally read-only:

- `sdlc_context_normalizer`
- `sdlc_planning_analyst`
- `sdlc_plan_author`
- `sdlc_plan_reviewer`

The Context Normalizer establishes a validated requirements boundary before repository analysis begins. The Python workflow, not any agent, owns orchestration and artifact persistence.

Each profile declares both `role: reviewer` and an explicit `allowedTools` list. The explicit list is authoritative.

## Validate

From the repository root:

```bash
cao profile validate .agentic-sdlc/cao/profiles/context-normalizer.md
cao profile validate .agentic-sdlc/cao/profiles/planning-analyst.md
cao profile validate .agentic-sdlc/cao/profiles/plan-author.md
cao profile validate .agentic-sdlc/cao/profiles/plan-reviewer.md
```

## Install

```bash
cao install .agentic-sdlc/cao/profiles/context-normalizer.md
cao install .agentic-sdlc/cao/profiles/planning-analyst.md
cao install .agentic-sdlc/cao/profiles/plan-author.md
cao install .agentic-sdlc/cao/profiles/plan-reviewer.md
```

## Verify installation

```bash
cao profile list
cao profile show sdlc_context_normalizer
cao profile show sdlc_planning_analyst
cao profile show sdlc_plan_author
cao profile show sdlc_plan_reviewer
```

## Safety

Do not launch these profiles with `--yolo`: that overrides their tool restrictions.

These profiles intentionally omit `@cao-mcp-server`; they are workers, not orchestrators. Current CAO enforces `assign` and `handoff` permissions at the MCP boundary using the caller's effective `allowedTools` policy.
