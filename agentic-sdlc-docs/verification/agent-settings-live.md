# Per-profile provider and model: live checks (2026-11-19)

Result: a model declared for one profile in `agent_settings` reached exactly that profile's agent; the other profiles
ran as before, and every step recorded the settings it used (step 2 of
[the agent settings plan](../plans/per-project-agent-settings.md)). A `default` model reached every agent and the
run's report recorded the settings with the project file's hash (step 3).

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

## Run `source-review-settings-2` (step 3)

Installed library `20bf16cb251a6311` (`feature/agent-settings` `c673b58`). Temporary key, restored right after:
`"agent_settings": {"default": {"model": "haiku"}}`. Same fixture, approved by the user. 23:30:23-23:32:57, five
steps in one attempt each; routing unchanged (one `AUTO_FIX`, one `HUMAN_REQUIRED`).

- All five terminals started with `--model haiku`; all five `agent-settings.json` files say `haiku`, `model_from:
  default`.
- `code-review.json` has `agent_settings` with `default_provider: claude_code`, all 18 profiles (a default applies to
  every profile) with `haiku` from `default`, and `project_file_sha256` `e14e7a4c…`, equal to the SHA-256 of the edited
  project file.

