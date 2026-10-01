# Parallel workers: worktree spike (2026-10-01)

Result: a Delivery worker started in a Git worktree is confined to that worktree by the existing write-scope hook,
with no workspace-trust or permission prompt. Decisions D1 and D8 of
[the parallel-workers plan](../plans/parallel-hybrid-workers.md) hold without any hook change.

## Setup

- A throwaway clone of `feature/parallel-hybrid-workers` at `8f17fb4`, with one worktree added by
  `git worktree add -b sdlc-work/spike/T1 .agentic-sdlc/runtime/spike/worktrees/T1 HEAD`.
- Installed profiles matched the repository (`sdlc_implementer` reinstalled from `main` `ee6658a`).
- A one-step ad-hoc workflow, installed temporarily as `sdlc_spike_worktree` and removed afterwards, called the
  repository's own `runtime._run_delivered_step` with `agent="sdlc_implementer"`, `repo=<worktree>` (the CAO working
  directory) and the answer directory `<worktree>/.agentic-sdlc/runtime/answer`.
- The prompt asked the agent to try four writes once each and report the outcome. Python then checked the disk.

## What happened

Run `spike-worktree-1` completed in 36 s with no prompt.

| Attempt | Agent report | On disk |
|---|---|---|
| `<worktree>/app/spike_worktree.txt` | written | yes |
| `<main clone>/app/spike_main.txt` | denied by the hook (outside the worker's roots) | no |
| `<worktree>/.agentic-sdlc/cao/spike.txt` | denied by the hook (`.agentic-sdlc/cao` is never writable) | no |
| `<main clone>/.agentic-sdlc/runtime/spike_main_runtime.txt` | denied by the hook | no |
| Answer file `<worktree>/.agentic-sdlc/runtime/answer/spike-worker.answer.json` | written | yes, read and stabilized by `runtime.py` |

Git afterwards:

- In the worktree, `git status --porcelain` showed only `?? app/spike_worktree.txt`. The answer files are ignored
  there too, so staging the source roots does not pick them up.
- In the main clone, `git status --porcelain` was empty: the worktree under the ignored `.agentic-sdlc/runtime/` is
  invisible to it.

## What this confirms

- The hook runs from the worker's working directory (`python3 .claude/hooks/...` is relative), so a worktree worker
  uses the worktree's copy of the hook, the registry and the settings, and its roots resolve inside the worktree.
- A worker in a worktree cannot write the main checkout, including the main runtime directory. Its answer file must
  live in the worktree (D8).
- CAO accepts a working directory under the repository's runtime directory, and the Claude Code provider starts there
  without a trust prompt.

## Not covered

- Two or more workers at the same time (plan step 7).
- A worktree whose hook or registry differs from the main checkout (D9 is a Python check, tested in step 3).
- CAO accepts ad-hoc workflow scripts only from its workflows directory, so a future spike also needs a temporary
  install there.
