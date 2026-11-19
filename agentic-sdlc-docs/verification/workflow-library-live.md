# Frozen workflow library: live checks (2026-11-19)

Result: all five workflows installed as small entry scripts over one frozen library on CAO 2.5.4. Planning and
Source review ran from the library; a changed library file stopped a run before any step (step 1 of
[the workflow library plan](../plans/workflow-library-and-prompt-files.md)). Delivery and the follow-up, which could
not run on CAO 2.5.4 before, ran end to end from the library (step 2).

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

## Step 2: Delivery and the follow-up

Same installation (library `bc242cd5ecfe82ca`). Before Delivery, the previous delivery branch `sdlc/PAY-DEMO-001`
(`efa6055`, the approved run 10) was renamed `archive/sdlc-PAY-DEMO-001-deliver10`, so the run created a new branch from
`main`.

| Run | Workflow | What it showed |
|---|---|---|
| `deliver-PAY-DEMO-001-11` | `sdlc_deliver`, `PAY-DEMO-001` (plan run 24), hybrid | 22:53:56-23:07:00. All 21 steps in one attempt: dispatch (one JSON repair turn), seven workers, integration, three PR review rounds each with two standards batches, two remediations. Verification passed. Ended `AWAITING_HUMAN_REVIEW` at `ffa1f8e` with two `DEVELOPER_REQUIRED` findings (missing T7 concurrency evidence, MEDIUM; exception-chain plan deviation, LOW), similar to run 10. `bundle.manifest_sha256` equals the SHA-256 of the installed library's manifest (D9). The run's records were copied to `.agentic-sdlc/runtime/deliver-PAY-DEMO-001-11-records/` and the tracked run-10 records restored, so `main`'s records are unchanged; the code stays on the new local `sdlc/PAY-DEMO-001`, not reviewed by a person. |
| `followup-pr3-4` | `source_followup`, closed PR #3 | Ran from the library and refused before any agent: "PR must be open" (by design). |
| `followup-library-1` | `source_followup`, local fixture (`agentic-sdlc-local-inputs/source-followup/create_fixture.py`) | 23:08:25-23:09:19. Regression, standards and judge steps in one attempt each. Every verdict equals `expected.json`: T1, T2 and G6 `ADDRESSED`, T3 `NOT_ADDRESSED`, T4 `PARTIALLY_ADDRESSED`, T5 `DISPUTED`; the resolved thread and the developer's own thread skipped; `COMMENTS-1` violated in `invoice.py` by the rename, `NAMING-1` not violated. |

