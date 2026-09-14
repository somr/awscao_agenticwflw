# CAO Planning Workflow Profiles

These are the canonical, version-controlled CAO profiles for Planning Workflow 1.

All four profiles currently use `claude_code` and are intentionally effectively read-only:

- `sdlc_context_normalizer`
- `sdlc_planning_analyst`
- `sdlc_plan_author`
- `sdlc_plan_reviewer`

The Context Normalizer establishes a validated requirements boundary before repository analysis begins. The Python workflow, not any agent, owns orchestration and artifact persistence.

Each profile declares both `role: reviewer` and an explicit `allowedTools` list. The explicit list is authoritative.

**Write scope.** Each profile grants `fs_write` so its agent can save its final JSON/Markdown answer to a file (the delivery mechanism `dev_plan.py` uses instead of parsing terminal output — see "Answer file delivery & the write-scope hook" in `cao/workflows/README.md` for the full rationale and why this matters given these agents read untrusted external content). This is not a broad write grant: a `PreToolUse` hook in this repository's `.claude/settings.json` denies any write outside `.agentic-sdlc/runtime/**`, which is enforced independently of CAO's own `allowedTools` and of `--dangerously-skip-permissions`. Do not remove `fs_write` from a profile without also updating `dev_plan.py`'s prompt-building for that step, and do not narrow or remove the hook without keeping an equivalent restriction in place.

`cao profile validate` currently prints an advisory `[warn] allowedTools entry 'fs_write' is not in CAO's recognized vocabulary` for all four profiles. This is a known limitation of the installed validator (`cli_agent_orchestrator/services/profile_validator.py`), which derives its recognized-vocabulary set only from the CLI's built-in role presets rather than the full CAO tool vocabulary — `fs_write` is confirmed present and mapped for the `claude_code` provider in `cli_agent_orchestrator/utils/tool_mapping.py`, and the warning does not affect exit status (0) or install.

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
