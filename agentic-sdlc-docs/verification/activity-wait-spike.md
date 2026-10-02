# Activity-based answer wait: spike (2026-10-02)

Result: a terminal's streamed output, read with CAO's `GET /terminals/{id}/output`, changes on every few-second
sample while Claude Code works and stays identical while it is idle. CAO's terminal status does not: it read
`idle` while the agents were working. The output buffer is therefore the activity signal for the answer wait in
`runtime.py`, and the status is not.

## Method

A sampler read each terminal's status and the SHA-256 of its output buffer every 2-3 seconds. The terminals
belonged to the live planning runs `plan-roots-A` and `plan-roots-B` (CAO 2.5.0, Claude Code).

| Terminal | Phase | Samples | Status reported | Output changed |
|---|---|---|---|---|
| `sdlc_context_normalizer` | working, about 25 s | 8 (3 s apart) | `idle` | on every sample |
| same | finished, until cleanup | 3 | `completed` | never |
| `sdlc_planning_analyst` | working, about 90 s | 43 (2 s apart) | `idle` | on every sample |
| same | finished, until cleanup | 3 | `completed` | never |
| `sdlc_plan_reviewer`, launched with no task | idle, 2 minutes | 40 (3 s apart) | `idle` | never |

The buffer is capped at 32,768 characters but keeps changing as it rolls. When the workflow exits and deletes a
terminal the buffer changes once more, then the terminal is gone (HTTP 404). That happens only after the answer
has been accepted.

## What was built from it

`_wait_for_answer_file` counts a poll as activity when the output digest changed, the answer file changed, or
CAO reports `processing`. It fails after `COMPLETION_INACTIVITY_SECONDS` (300 s) without activity, and never later
than the step budget (`STEP_TIMEOUT_SECONDS`, 30 minutes). Completion is still only the answer file staying
identical for two polls. A failed output read never counts as activity. Each poll's activity is recorded in
`<step>.stabilization.json`, together with why the wait stopped (`inactive` or `step_budget`).

## Live check of the built wait

Run `spike-inactive-wait-1` called `runtime._run_stabilized_step` from a clone of `feature/follow-ups` at
`43feb5b`, through an ad-hoc workflow installed temporarily and removed afterwards. It asked
`sdlc_plan_reviewer` to reply in chat and write no file, so no answer ever appeared.

- The step started at 12:34:11 and the wait stopped at 12:39:41 with `IncompleteAgentExecutionError`
  ("showed no activity for 300s"). The old fixed wait would have lasted 30 minutes.
- `spike-inactive.stabilization.json`: `stopped: inactive` after 104 polls. One poll recorded `output` activity
  (the agent finishing its reply), and the other 103 recorded none. The status read `completed` on every poll.
- Normal work is unaffected. Delivery run `deliver-activity-1`, with all four workflows reinstalled from `43feb5b`,
  reached `AWAITING_HUMAN_REVIEW`: all 10 agent steps stabilized within two polls, and every poll records its activity.

## Not covered

- A tool call that takes longer than the inactivity limit without any redraw. Claude Code's spinner and timer
  redraw while a tool runs, but this was not measured for a multi-minute call.
- Providers other than Claude Code.
