# Hybrid delivery: live verification (2026-09-20)

Result: the hybrid implementation path works end to end against the real CAO server and
Claude Code. Skills and the verification-repair path were **not** exercised (see the end).

## Setup

- Delivery profiles (`code-supervisor`, `implementer`, `pr-reviewer`, `remediator`) and the
  `sdlc_deliver` bundle were reinstalled from `main` at `3d9c573`; each installed copy was
  byte-identical to the repository.
- Run in a throwaway clone with no remote, so the real repository, its `sdlc/PAY-DEMO-001`
  branch and its records were untouched. The clone held the originally human-approved
  PAY-DEMO-001 plan records (plan hash, approval and baseline all verified) and the sample
  application in its original "before" state.
- `cao workflow run sdlc_deliver --wait --json --run-id deliver-hybrid-1 --input ticket_id=PAY-DEMO-001
  --input repository_root=<clone> --input base_branch=main --input implementation_mode=hybrid`

## What happened

The run ended `AWAITING_HUMAN_REVIEW` in 5.7 minutes with no remediation rounds. Step timings, from
the answer files' modification times:

| Step | Time |
|---|---|
| `dispatch-v1` (code supervisor) | 52 s |
| `worker-1` to `worker-4` | 33, 35, 37, 48 s (about 153 s in total) |
| `integrate-v1` (implementer) | 60 s |
| `pr-review-r1` (independent reviewer) | 64 s |
| Python (commit, verification, artifacts) | about 12 s |

- **Dispatch.** The supervisor turned the approved plan's six tasks into four ordered assignments
  on the `developer` worker (`T1 -> T2 -> T3 -> T4`, no skills). It folded the plan's inspection-only
  task into another assignment, and Python's verification stood in for the plan's final regression task.
  The chain is sequential because the plan's own dependencies make it so; see
  [hybrid delivery](../workflows/hybrid-delivery.md) for why workers run one after another.
- **Delivery.** One commit, `[PAY-DEMO-001] Implement approved plan (hybrid)`, touching only
  `app/payment_service/{payment_service,repository}.py` and `app/tests/test_payment_service.py`
  (+155/-3). `delivery-manifest.json` records `implementation_mode: hybrid`.
- **Verification.** Python ran the two registered commands (`compileall`, `unittest discover`); both passed.
  Independently: 7 tests pass on the delivered branch, and the same 7 tests fail 4 times against the
  original code, so they exercise the change.
- **Honest reporting.** All four workers and the integrator disclosed that they cannot execute
  commands, that the plan's one-time manual hardening check was not performed, and named residual risks.
  The independent reviewer then raised three LOW findings (a release failure masking the original error,
  a weak timing assertion, and the missing manual check), all `DEVELOPER_REQUIRED`, matching those
  disclosures. Nothing was eligible for automatic fixing.

## Not exercised

- **Skills.** No skill was selected, so skill injection and the skills' extra verification suites
  (`npm`, `spark-submit`) have not run live. `required-skills.md` was written but is empty.
- **Repair and remediation.** Verification passed first time and no finding was auto-fixable, so the
  verification-repair turn and remediation with skill context are covered only by tests.
- **Parallelism, `single` mode, and human review** of the result were not part of this run.
