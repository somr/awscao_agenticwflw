# Consolidated branch: live verification (2026-10-02)

Result: after `feature/parallel-hybrid-workers` was merged into `feature/project-config` (`3ebeb88`, which
already held the per-project configuration file and the Source remediation workflow), the write boundary,
parallel Delivery and Source remediation all worked live against the real CAO server and Claude Code.

## Setup

- Installed from `3ebeb88`: all 15 profiles (including the changed `sdlc_remediator` and the new
  `sdlc_source_fix_reviewer`), `sdlc_deliver`, `source_review` (upgrade steps) and `source_remediate`.
  `sdlc_dev_plan` already matched. Every installed copy was compared byte for byte with the repository.
- Each check ran in its own throwaway clone of `feature/project-config`. Nothing was published or pushed.

## 1. Write boundary from `agentic-sdlc-project.json`

The clone's project file set the only source root to `billing/src`.

- One-step run `spike-project-boundary-1` (an ad-hoc workflow installed temporarily and removed afterwards):
  `sdlc_implementer` wrote `billing/src/Spike.java`, and the hook denied `app/spike.py`. The agent did not
  attempt the project file or the registry, because its profile forbids it, so those two were tested directly.
- The hook piped against a live `sdlc_implementer` terminal (`e41efbcb`, from the Delivery run below), from the clone:

| Path | Decision |
|---|---|
| `billing/src/Pipe.java` | allowed |
| `app/pipe.py` | denied: "restricted to .agentic-sdlc/runtime/** (plus billing/src/** for profile 'sdlc_implementer')" |
| `agentic-sdlc-project.json` | denied: never permitted for CAO workers |
| `.agentic-sdlc/cao/specialists.json` | denied: never permitted for CAO workers |
| `billing/src/Pipe.java` with `source_roots` also added to the registry | denied: the configuration is invalid (key set in both files) |

## 2. Parallel Delivery with the project file

Run `deliver-consolidated-1` on PAY-DEMO-001 (default `hybrid_max_parallel=4`), 11:02:15 to 11:12:01 UTC.

- The waves were `[T1,T3] [T2,T6] [T4,T5] [T7]`. Two implementer terminals ran at the same time in separate
  worktrees, and the six task commits merged in task order without conflicts.
- Verification used the `application` suite from `agentic-sdlc-project.json`. The run's `registry.json` evidence
  shows the merged settings, with no unavailable skills, and verification passed the first time.
- Review round 1 raised a LOW `AUTO_FIX` finding and a MEDIUM `DEVELOPER_REQUIRED` finding. One remediation round
  fixed the LOW one, and round 2 left the MEDIUM one for a developer. The run ended `AWAITING_HUMAN_REVIEW`.
- No worktree or `sdlc-work/*` branch was left behind, and all app tests pass on `sdlc/PAY-DEMO-001`.

## 3. Source remediation (first live run)

- Fixture: on top of `3ebeb88`, a base commit added a correct `app/pagination.py` and `app/accounts.py`, a test,
  and a project file whose `application` suite runs only that test. The head commit then introduced an
  off-by-one (`items[:limit + 1]`) and removed the owner check from `read_account`.
- `source_review` in local fixture mode (`review-fixture-consolidated-1`, status `REVIEWED`) found both: the
  off-by-one as MEDIUM `AUTO_FIX`, and the missing owner check as HIGH `HUMAN_REQUIRED`.
- `source_remediate` (`fix-fixture-consolidated-1`) created `sdlc/source-remediate/fix-fixture-consolidated-1`:
  - baseline verification;
  - one fix round, which reverted the slice and added two regression tests;
  - verification passed;
  - the independent fix review marked the finding `FIXED`.
  The human finding was not attempted. The state is `AWAITING_HUMAN_REVIEW`, with `publication_safe: true`.

Observations for the workflow's owner, none blocking:

- The brief says "Candidate cleared for publication: True" next to a coverage gap saying that the PR is not
  safe to publish or merge. The first refers to publishing the fix replies, the second to the PR as a whole;
  the wording should make that distinction clear.
- The coverage gaps in the brief repeat two observations (callers of `read_account` and `first_items`) in
  nearly the same words.
- `cao workflow result` shows no output for a detached run of any workflow, so the records path printed by the
  workflow is only visible with `--wait --json` or in the manifest.

## Not covered

- Publishing remediation results to GitHub and pushing (`publish_source_remediation.py`).
- A remediation run that needs more than one round or ends `BLOCKED`.
- A Delivery run on a project whose roots are outside `app/` from start to finish. The boundary check above
  covers the hook, and the unit and integration tests cover Delivery with other roots.
