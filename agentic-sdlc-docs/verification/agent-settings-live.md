# Per-profile provider and model: live checks (2026-11-19)

Result: a model declared for one profile in `agent_settings` reached exactly that profile's agent; the other profiles
ran as before, and every step recorded the settings it used. Step 2 of
[the agent settings plan](../plans/per-project-agent-settings.md).

## Setup

`feature/agent-settings` `65c48b8` (rebased onto `main` `36c164a`), installed as library `57f24b1529586187` for all
five workflows; CAO 2.5.4, Claude Code 2.1.327. For the run only, `agentic-sdlc-project.json` got one uncommitted key,
restored right after the run:

```json
"agent_settings": {"profiles": {"sdlc_source_mapper": {"model": "haiku"}}}
```

## Run `source-review-settings-1`

Source review on the local fixture from `agentic-sdlc-local-inputs/source-review/create_fixture.py` (the same base and
head as `source-review-library-1`), approved by the user with `cao workflow approve`. 23:21:20-23:23:55, five steps in
one attempt each. Routing as expected: the slice defect `AUTO_FIX` (CORRECTNESS, MEDIUM), the removed ownership check
`HUMAN_REQUIRED` (AUTHN_AUTHZ, HIGH).

| Step | `<step_id>.agent-settings.json` | Terminal launch (CAO terminal log) |
|---|---|---|
| `source-mapper` | `claude_code`, model `haiku`, `model_from: profile` | `claude --dangerously-skip-permissions --session-id … --model haiku …` (terminal `2b9119ef`, the first one created) |
| `source-correctness` | `claude_code`, model `null`, `unset` | no `--model` |
| `source-security` | `claude_code`, model `null`, `unset` | no `--model` |
| `source-validator` | `claude_code`, model `null`, `unset` | no `--model` |
| `source-feedback` | `claude_code`, model `null`, `unset` | no `--model` |

The two parallel reviewer terminals (`67b3fa33`, `1a1eff52`) are correctness and security; neither names a model, so
which is which does not matter here. Without the key, the step calls are unchanged (covered by tests; no live run
needed).
