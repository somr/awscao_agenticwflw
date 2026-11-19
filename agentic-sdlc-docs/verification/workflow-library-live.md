# Frozen workflow library: live checks (2026-11-19)

Result: all five workflows installed as small entry scripts over one frozen library on CAO 2.5.4. Planning and
Source review ran from the library; a changed library file stopped a run before any step. Step 1 of
[the workflow library plan](../plans/workflow-library-and-prompt-files.md). Delivery and the follow-up are step 2.

## Installation

From `feature/workflow-library` `bc67923` (library id `bc242cd5ecfe82ca`; CAO 2.5.4, Claude Code 2.1.327). No run was
active. Rollback copies of the previous workflow scripts and of the Source review and follow-up profiles were kept in
`.agentic-sdlc/runtime/install-rollback/2026-11-19-library/`.

| Workflow | Installed size before | Entry size now |
|---|---|---|
| `sdlc_dev_plan` | 105,964 | 4,774 |
| `sdlc_deliver` | 167,030 (could not be reinstalled on 2.5.4) | 4,774 |
| `source_review` | 111,700 | 4,616 |
| `source_remediate` | 113,837 | 4,405 |
| `source_followup` | 138,288 (could not be reinstalled on 2.5.4) | 4,831 |

- Source review used its [upgrade procedure](../workflows/source-review.md#upgrading) (workflow and six profiles
  removed, then installed).
- The follow-up installer stopped: CAO 2.5.4 refuses to replace a profile installed by an earlier CAO ("installed copy
  without CAO source provenance"). Both installed follow-up profiles were byte-identical to the repository's; they were
  deleted and the installer rerun, as CAO's message advises.
- CAO 2.5.4 adds an `x-cao-source-stem: '<file stem>'` line to each profile it installs. With that line ignored, all
  18 installed profiles match the repository (the check in
  [build-and-install.md](../build-and-install.md#check-that-cao-matches-the-repository) now ignores it).
- The documented check found all five entries pointing to `sdlc-lib/bc242cd5ecfe82ca`.

## Runs

Each new entry (and, for reasons CAO does not show, a later run of the same entry with the same inputs) needed the
user's `cao workflow approve`.

| Run | Workflow | What it showed |
|---|---|---|
| `plan-library-guard-1` | `sdlc_dev_plan`, `PAY-DEMO-001` (plan already approved) | The traceback names `…/sdlc-lib/bc242cd5ecfe82ca/sdlc_workflows/planning.py`; the configuration checks ran and the run stopped at the approved-plan guard ("an APPROVED plan already exists"), with no steps. Nothing written to the working tree. |
| `source-review-library-1` | `source_review`, local fixture (`create_fixture.py`) | Mapper, correctness, security, validator and feedback each completed in one attempt. Slice defect `AUTO_FIX` (CORRECTNESS, MEDIUM), removed ownership check `HUMAN_REQUIRED` (AUTHN_AUTHZ, HIGH), as the guide expects. `coverage_status` `INCOMPLETE` from the agents' gaps (the fixture's functions have no callers in the snapshot). The project rules did not match the fixture's files and were skipped. Records in `agentic-sdlc-records/source-review/local-688361c00433/source-review-library-1/`. |
| `plan-library-tamper-1` | `sdlc_dev_plan`, after appending a comment line to the installed library's `errors.py` | Failed with no steps: "Workflow library …/bc242cd5ecfe82ca does not match this installed workflow: errors.py changed. Reinstall the workflow; a run started from the old library cannot resume." The original bytes were restored and the library re-verified. |
