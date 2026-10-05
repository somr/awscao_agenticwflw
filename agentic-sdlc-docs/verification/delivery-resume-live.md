# Resume a BLOCKED delivery: live verification (2026-10-05)

Result: a Delivery run that ended `BLOCKED` was continued by a second run with `resume=true` at both resume points,
against the real CAO server and Claude Code agents. Resuming from `verification` ran no implementation step and
reviewed a hand-made fix; resuming from `implementation` ran only the unfinished tasks. Both ended
`AWAITING_HUMAN_REVIEW`. Plan: [delivery-resume.md](../plans/delivery-resume.md).

## Setup

- A throwaway clone of `main` (`84d7d80`) in the session scratchpad, holding the human-approved PAY-DEMO-001 plan and
  the sample application in its "before" state; the earlier delivery records removed; no remote.
- So that the production `sdlc_deliver` stayed equal to `main`, the branch's bundle was installed as a separate
  workflow, `sdlc_deliver_resume`, built with `build_workflow.py deliver` from each step's commit. No profile changed.
- Base branch `main` of the clone, default `hybrid_max_parallel=4`.

## Runs

| Run | Bundle | What was done | Outcome |
|---|---|---|---|
| `deliver-resume-live-1` | `6b0e9b7` | The clone's `agentic-sdlc-project.json` got an extra verification command that always fails, printing a Maven-style "could not resolve dependencies ... declared in pom.xml" error. | `BLOCKED`, `repair_changed_nothing`, resume point `verification` |
| `deliver-resume-live-2` | `7915943` | A hand commit on `sdlc/PAY-DEMO-001` removed the failing command (a change outside the source roots, standing in for a pom.xml fix); then `resume=true`. | `AWAITING_HUMAN_REVIEW` |
| `deliver-resume-live-3` | `21de628` | Fresh delivery; after wave 2 an uncommitted edit to `agentic-sdlc-project.json` made wave 3's worktree check fail. | `BLOCKED`, `hybrid_implementation_failed`, resume point `implementation` |
| `deliver-resume-live-4` | `bddcaa7` | The edit was discarded; then `resume=true`. | `AWAITING_HUMAN_REVIEW` |

## What each run showed

**Run 1 (what a BLOCKED manifest records).** The supervisor's 7 tasks ran in 4 waves (`[T1,T3] [T2,T6] [T4,T5] [T7]`),
6 task commits merged, T7 changed nothing. Verification failed on the extra command; the repair turn, which now saw the
command's output, changed nothing, so the run ended `BLOCKED` with `repair_changed_nothing` (before this work: run
state `failed`). The manifest held `schema_version` `1.1`, `reason`, `resume_point`, `branch_head_sha`, `base_sha`,
the bundle and registry hashes, the 7 tasks with each status and commit, and the error text in the failed command's
`output_tail`.

**Run 2 (resume from verification).** Steps run: `pr-review-r1`, `remediate-r1`, `pr-review-r2`; no supervisor,
worker or repair step. Verification passed with the corrected settings. The manifest recorded `resumed_from`
(run 1, `repair_changed_nothing`, `verification`), the hand commit with its path `agentic-sdlc-project.json`,
`base_drift` with 0 commits behind, and totals of 2 runs, 1 repair turn and 1 remediation round. The PR body and the
brief listed the hand commit. The reviewer raised a `DEVELOPER_REQUIRED` finding that the hand fix removed the failing
check instead of fixing the build, so a hand change gets the same independent review as an agent's.

**Run 3 (BLOCKED part-way through implementation).** Waves 1 and 2 merged T1, T3, T2 and T6; wave 3 stopped before any
worker started with "agentic-sdlc-project.json in worktree T4 differs from the main checkout". The manifest held the
4 finished tasks, their commits in `delivery_commits`, resume point `implementation`, and the next-action hint.

**Run 4 (resume from implementation).** Steps run: `worker-T4`, `worker-T5` (in parallel), `worker-T7`,
`integrate-v1`, `pr-review-r1`, `remediate-r1`, `pr-review-r2`; no supervisor and none of the 4 finished tasks.
The manifest lists all 7 tasks (T7 `no_changes`), 6 task commits plus the remediation commit, each once, and totals of
2 runs. The sample application's tests pass on the delivered branch.

## Not exercised live

The `base_drift_overlap` stop, the refusals (uncommitted changes, a missing recorded commit, changed roots, a rejected
task list) and `resume_redispatch` are covered by `tests/test_delivery_resume.py` in both the bundle and the source
modules, not by a live run.
