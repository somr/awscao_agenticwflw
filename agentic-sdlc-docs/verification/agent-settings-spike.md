# Per-call model: spike (2026-11-18)

Result: a `model` passed to `cao_workflow.step()` reaches the agent's command line for both Claude Code and Codex,
and the write-scope hook still applies to a Claude Code agent started that way. Step 0 of
[the agent settings plan](../plans/per-project-agent-settings.md) is done; no CAO change is needed.

## Method

A one-step workflow, installed temporarily as `sdlc_tmp_agent_settings_spike` (and removed afterwards), called
`step(provider, "sdlc_source_mapper", prompt, recovery="idempotent", model=<model>, working_directory=<dir>)` and
returned the agent's answer. The prompt asked the agent to state its model and, in the Claude run only, to try one
write to `spike-probe.txt`. Both plans were approved by the user with `cao workflow approve`. CAO 2.5.4,
Claude Code 2.1.327, codex-cli 0.170.0.

| Run | Provider, model | Directory | Launch command (terminal log) | Agent's answer |
|---|---|---|---|---|
| `agent-settings-spike-1` | `claude_code`, `haiku` | repository root | `claude --dangerously-skip-permissions --session-id … --model haiku --append-system-prompt-file …` | model `claude-haiku-5-5`; write denied by `PreToolUse:Write` hook ("CAO worker writes are restricted to .agentic-sdlc/runtime/** …"); no file created |
| `agent-settings-spike-codex-1` | `codex`, `gpt-6-luna` | empty scratch folder | `codex --yolo --no-alt-screen --disable shell_snapshot --model gpt-6-luna …` | model "GPT-6" (the agent does not know its exact id; the launch command is the evidence); no write attempted |

Each run took about 20 seconds.

## What it means for the plan

- D2/D4: `model` can be sent per call for any profile; nothing has to be installed per project.
- D3 stays as agreed: the Codex adapter launches with `--yolo` (sandbox and approvals off) for a profile without
  `codexProfile`, and the Claude write-scope hook does not apply to it. The Codex run was read-only in an empty
  folder for that reason.
- A workflow with a new spec or new inputs needs `cao workflow approve` by a human before its first run.
