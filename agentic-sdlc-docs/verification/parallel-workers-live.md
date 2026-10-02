# Parallel hybrid workers: live verification (2026-10-01)

Result: Delivery ran PAY-DEMO-001's independent tasks at the same time in separate worktrees, merged one commit per
task in task order and reached `AWAITING_HUMAN_REVIEW`. The implementation phase took 4 min 16 s, against 7 min 26 s
for the same plan run sequentially earlier that day.

## Setup

- A throwaway clone of `feature/parallel-hybrid-workers` at `bb9d4eb`, holding the human-approved PAY-DEMO-001 plan
  (plan run 24) and the sample application in its "before" state. `base_branch=feature/parallel-hybrid-workers`.
- `sdlc_code_supervisor` and `sdlc_implementer` were installed from `20de092`; both installed copies matched the
  repository. The other delivery profiles matched `main` `ee6658a`.
- So that the production `sdlc_deliver` stayed unchanged, the branch's bundle was installed as a separate workflow,
  `sdlc_deliver_parallel`, built with `build_workflow.py deliver`.
- `cao workflow run sdlc_deliver_parallel --run-id deliver-parallel-1 --input ticket_id=PAY-DEMO-001
  --input repository_root=<clone> --input base_branch=feature/parallel-hybrid-workers --input implementation_mode=hybrid`
  (default `hybrid_max_parallel=4`).

## Found and fixed before the run

The first install was not listed by CAO, and `cao workflow run` answered "unknown workflow", although
`cao workflow validate` reported it valid. The server log showed why: CAO reads `INPUTS` without executing the
script, and the new input's default was the name `MAX_PARALLEL_WORKERS`, not a literal. Fixed in `bb9d4eb` with a
literal default and a packaging test that parses every bundle's `INPUTS` with `ast.literal_eval`. The test fails
on the old code.

## What happened

The supervisor's assignments matched the plan's dependency table, and every task owned different files, so Python
added no dependencies:

| Wave | Tasks | Workspace | Answers written | Result |
|---|---|---|---|---|
| 1 | T1, T3 | own worktrees | 23:20:05, 23:20:09 | both merged (`ee9273f`, `52099b9`) |
| 2 | T2, T6 | own worktrees | 23:20:59, 23:20:56 | both merged |
| 3 | T4, T5 | own worktrees | 23:22:25, 23:22:12 | both merged |
| 4 | T7 | main checkout | 23:23:46 | no changes (a check-only task) |

- Dispatch was written at 23:19:30, and the integration pass answered at 23:24:55 without changing anything.
- Both workers of each wave ran at the same time: the status listed both as running, and their answers landed a
  few seconds apart.
- Each task changed only the files in its `owns`, and no cherry-pick conflicted. After every wave the worktrees and
  the `sdlc-work/deliver-parallel-1/*` branches were gone.
- Verification passed the first time.
- Review round 1 raised four findings. Python routed two LOW ones as `AUTO_FIX`, and one remediation round fixed both
  (`508d52d`). Review round 2 left two `DEVELOPER_REQUIRED` findings (MEDIUM testing, LOW plan deviation).
- The run ended `AWAITING_HUMAN_REVIEW` after 10 min 16 s. All app tests pass on the delivered branch.
- The manifest records `hybrid_max_parallel: 4` and the six task commits in merge order.

## Follow-up made after the run

Commit subjects used the supervisor's long `plan_reference` and were cut at 100 characters. From `a45f7ae` the
subject is `[<ticket>] <task>: implement hybrid task`, the plan reference is in the commit body, and the PR body lists
the task commits in merge order. Unit and integration tests cover this; it was not run live again.

## Not covered live

- A merge conflict and its sequential rerun, a worker failure inside a wave, and a changed write boundary (D9). All
  three are covered by tests against real temporary repositories (`tests/test_hybrid.py`, `tests/test_worktrees.py`).
- Overlapping `owns` that forces an added dependency (tests only).
- More than two workers at once, and a large project. PAY-DEMO-001's widest wave has two tasks.
