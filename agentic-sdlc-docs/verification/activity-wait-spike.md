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

## Not covered

- A tool call that takes longer than the inactivity limit without any redraw. Claude Code's spinner and timer
  redraw while a tool runs, but this was not measured for a multi-minute call.
- Providers other than Claude Code.
