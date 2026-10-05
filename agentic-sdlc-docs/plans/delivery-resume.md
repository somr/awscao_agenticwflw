# Plan: resume a BLOCKED Delivery run

Status: **in progress** (written 2026-10-05, branch `feature/delivery-resume`). Progress notes are at the end.

## 1. Why

A production trial (Scala, Maven, a plan of 28 tasks) ended `BLOCKED` after verification failed on a `pom.xml`
problem and the single repair turn did not fix it. Delivery cannot continue from there: a new run implements the
whole plan again on top of the branch, and a hand fix cannot be verified or reviewed by the workflow. The run ran
in a throwaway Docker container, so its evidence was lost as well.

## 2. Environment this must work in

- The container mounts `.git`, `agentic-sdlc-records/` and the source roots from the host. `.agentic-sdlc/`
  (bundle, registry, profiles, policies, `runtime/`) comes from the image and is lost when the container ends.
- So **the delivery manifest is the only resume input**, together with the commits on `sdlc/<ticket>`. Nothing
  may be read from `.agentic-sdlc/runtime/` of an earlier run. That also keeps the trust model simple: agents may
  write in `runtime/`, but never in `agentic-sdlc-records/`.
- Resume starts from a **defined end state** written by Python (`BLOCKED` with a reason). Resuming after a crash
  (`failed` run, killed container) is out of scope; CAO's own `cao workflow resume` does not apply either, because
  it refuses finished runs and does not restore Git side effects.

## 3. Decisions (agreed 2026-10-05)

| # | Decision |
|---|---|
| D1 | The manifest records a bundle identity (hash of the bundle's module manifest, `null` when run from source) and a hash of the merged registry and project settings, plus its own format version. |
| D2 | On resume the saved task list is re-checked with the current bundle's rules and registry; resume proceeds when it passes. Opt-in fallback: let the supervisor assign only the remaining work. |
| D3 | Only manifests written in the new format (`schema_version` `1.1`) can be resumed. |
| D4 | Commits added by hand after the recorded branch head are allowed anywhere (for example a `pom.xml` outside the source roots). They are listed with their paths in the manifest, the PR body and the human review brief, and the review covers the whole diff. |
| D5 | If the source roots changed, resume proceeds only when every recorded delivery commit still lies inside the new roots. |
| D6 | Base drift: always record the base commit at the start, the base commit at resume, the commits on the base branch that the delivery branch lacks and the files they share with the delivery. No shared files: continue. Shared files: end `BLOCKED` with `base_drift_overlap`; a human merges the base branch into the delivery branch by hand (a merge keeps the recorded commits) or plans again, then resumes. Never rebase. |
| D7 | Retry budgets (repair turn, remediation rounds) start again on each resume; the manifest keeps totals across runs. |
| D8 | Resumable: every `BLOCKED` reason, including the new `repair_changed_nothing`. Later, maybe: a fresh review from `AWAITING_HUMAN_REVIEW` after a hand fix. |

## 4. Design

### 4.1 Manifest additions (format `1.1`)

Written on every state change, and complete whenever the run ends `BLOCKED`:

| Field | Content |
|---|---|
| `reason` | for every `BLOCKED` exit (today only `hybrid_implementation_failed` has it) |
| `resume_point` | `implementation` or `verification`: where a resume continues |
| `branch_head_sha` | the delivery branch head when the run ended |
| `base_sha` | the base branch tip when the delivery branch was created (carried over by a resume) |
| `bundle` | `{"manifest_sha256": ..., "registry_sha256": ...}` (D1) |
| `hybrid` | `dispatch` (the supervisor's tasks), `waves`, and per task: `status`, `commit`, `changed_paths`, `outside_owns`, `wave`, `result`; also filled when implementation fails part-way |
| `verification.commands[].output_tail` | last part of stdout/stderr of each failed command |
| `resumed_from` | `{run_id, state, reason, resume_point}` of the run this one continued |
| `hand_commits` | `[{sha, subject, paths}]` added after the recorded head |
| `base_drift` | D6 facts |
| `totals` | repair turns, remediation rounds and runs across the resume chain (D7) |

### 4.2 Resume run

New input `resume` (default `false`). With `resume=true`, before anything is written:

1. Read `delivery-manifest.json`. Require format `1.1`, state `BLOCKED`, a `resume_point`, the same ticket and the
   current plan hash. The implementation mode comes from the manifest.
2. Usual plan checks (approved, hash, baseline ancestry).
3. Check out the existing `sdlc/<ticket>`; require no tracked changes and an empty index.
4. Every recorded commit and `branch_head_sha` must be ancestors of `HEAD`. `branch_head_sha..HEAD` are hand commits
   (D4). Recorded commits must lie inside the current source roots (D5).
5. Base drift (D6). Overlap ends `BLOCKED` with `base_drift_overlap`, keeping the earlier `resume_point`.
6. Continue at the resume point:
   - `verification`: run verification (and a repair turn when it fails), then PR, review, remediation as usual,
     with the recorded implementation summary and commits. Review files continue numbering after the earlier run.
   - `implementation` (hybrid): re-check the saved tasks (D2); skip tasks whose commit is in `HEAD`; run the rest
     in waves; then integration and verification.
7. The new manifest carries `resumed_from`, `hand_commits`, `base_drift` and the totals.

## 5. Steps

Each step: tests in both modes (bundle and source), then a live check in a throwaway clone under the session
scratchpad (CAO refuses `/tmp`), installing the bundle from the branch first.

1. **The manifest records enough.** Every field of 4.1 except `resumed_from`/`hand_commits`/`base_drift`;
   `run_hybrid` reports partial progress when it stops; failed commands keep an output tail, which the repair prompt
   now shows too; a repair turn that changes nothing ends `BLOCKED` (`repair_changed_nothing`) instead of `failed`.
   Live: a run whose verification always fails; inspect the manifest.
2. **Resume from verification.** Input `resume`, steps 4.2.1-5, continue at `verification`, hand commits and drift in
   the manifest, PR body and brief. Live: the run from step 1, a hand fix commit, `resume=true`, ends
   `AWAITING_HUMAN_REVIEW`; plus a drift-overlap case.
3. **Resume from implementation.** Skip finished tasks, run the rest, integrate. Live: stop a wave-2 worker's
   terminal so the run ends `hybrid_implementation_failed`, then resume.
4. **Opt-in re-dispatch** (D2 fallback) when the saved tasks no longer pass the current rules.
5. **Docs.** `delivery.md` (inputs, results, stop reasons, "After a `BLOCKED` run" becomes resume-first, manifest
   fields), `README` pointer if needed. Verification record in `agentic-sdlc-docs/verification/delivery-resume-live.md`.

Not in scope: crash resume, automatic merge or rebase of the base branch, pushing, re-review from
`AWAITING_HUMAN_REVIEW`.

## 6. Progress notes
- 2026-10-05, step 1 done (`6b0e9b7`). Live `deliver-resume-live-1` (bundle installed as `sdlc_deliver_resume`, throwaway
  clone of PAY-DEMO-001 with a verification command that always fails with a pom.xml-style error): 7 tasks in 4 waves,
  6 task commits, the repair turn changed nothing, run ended `BLOCKED` `repair_changed_nothing` (before: `failed`);
  the manifest held the tasks, commits, bundle identity and the error text in `output_tail`.
- 2026-10-05, step 2 done (`7915943`). Live `deliver-resume-live-2`: a hand commit on the branch removed the failing
  command from `agentic-sdlc-project.json`; `resume=true` ran only review, one real remediation round and a second
  review, ending `AWAITING_HUMAN_REVIEW`. The hand commit was listed in the manifest, PR body and brief, and the
  reviewer raised a `DEVELOPER_REQUIRED` finding that the hand fix removed the check instead of fixing the build.
- 2026-10-05, step 3 code done (`21de628`); live check next.
