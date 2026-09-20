# Plan: non-converged planning, developer guidance and warm start

Status: **in progress** (written 2026-09-20). M0 done (section 5a). M1 done and live-verified (run `plan-PAY-DEMO-001-20`). M2 (guidance) and M3 (reviewer history) implemented and unit-tested (155 tests, both modes); their live checks are folded into M5. M4 (warm start) implemented and unit-tested (160 tests, both modes). **M5 done: live end-to-end verified** (section 5f). Remaining: human approval of the demo plan, commit/merge, and the open questions in section 6.
Scope: Planning Workflow 1 (`sdlc_dev_plan`, `.agentic-sdlc/cao/sdlc_workflows/planning.py`) and its profiles, contracts and tests.

## 1. Problem

When the independent plan reviewer does not return `PASS` within `max_review_rounds`, planning stops
and produces nothing a human can act on. Observed in run `plan-PAY-DEMO-001-18` (2026-09-19):

- Three author/review rounds all returned `CHANGES_REQUIRED` (5, 6, then 4 findings; the last round's four were all LOW).
  The findings changed from round to round instead of shrinking.
- CAO reported the run as `completed`. `cao workflow status|result|events` and the run's DB row show step states only.
  The `AWAITING_HUMAN_CLARIFICATION` payload with the blockers was not retrievable from any place searched.
- Nothing was written to `sdlc-records/`. The last candidate plan and reviews exist only in the git-ignored
  `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-18/planning/`.
- The developer has no way to give the planner information it lacked. Re-running only re-rolls the dice.
  The only channel today is editing the Jira/Confluence sources (the fixture under `examples/PAY-DEMO-001/`),
  which is how runs 12 to 17 converged, but for a live ticket the developer may not own those sources.
- Cause of the churn: `build_reviewer_prompt` receives no earlier review, so the reviewer re-audits from scratch every round
  and a decision settled in round 1 can be re-raised in round 3. The author's revision prompt sees only the previous plan and one review file.
- No test covers any of the `_emit_human_needed` stop paths (`grep` for `review_convergence_limit_reached`,
  `plan_review_requires_human_decision`, `AWAITING_HUMAN_CLARIFICATION` in `tests/` finds nothing).

## 2. Decisions already made

Agreed with the user on 2026-09-19/20.

| # | Decision | Detail |
|---|---|---|
| D1 | Durable, non-approvable evidence on non-convergence | Written to `sdlc-records/<ticket>/candidates/<run-id>/`, tracked in Git, state `NOT_CONVERGED`. `approve_plan.py` and Delivery never accept it. |
| D2 | **No override.** Approval stays strict | Only a reviewer `PASS` yields an approvable plan. An override in `approve_plan.py` was proposed and rejected. Nothing was ever implemented, so there is nothing to revert. A reviewer `PASS` is a required gate, not the approval: a human still records approval with `approve_plan.py`. |
| D3 | Developer guidance | New optional input `guidance_file`, a human-authored Markdown file that shapes analysis, authoring and review. |
| D4 | Warm start | New optional input `resume_from`: continue from a previous candidate instead of running from scratch, to save tokens and give direction. |
| D5 | Reviewer sees earlier rounds | The reviewer receives the previous reviews (and, on a warm start, the candidate's reviews) and must account for each earlier blocking finding. |

Out of scope: multiple reviewers or a quorum, real Jira write-back of clarifications, automatic answering of findings by an agent,
changes to Delivery.

## 3. Design

### 3.1 Evidence when planning stops without `PASS`

Applies to every stop cause raised through `_emit_human_needed`: `review_convergence_limit_reached`,
`plan_review_requires_human_decision`, `renormalized_context_not_ready`, `context_not_ready`.

Layout (new; created by the workflow, never by an agent):

```text
sdlc-records/<ticket>/candidates/<run-id>/
  candidate-manifest.json   state NOT_CONVERGED, stop_cause, ticket_id, run_id, base_branch, repository_baseline_sha,
                            planning_context_sha256, sources_sha256, plan_sha256, review_rounds, context_versions,
                            guidance_sha256 (or null), sha256 of every file below, created_at
  human-needed.json         reason, blockers[] (finding id + description + plan_section + required_action),
                            suggested next steps (write guidance / re-run / warm start), run_id
  candidate-plan.md         last plan
  reviews/<k>-review-r<N>-c<V>.json   every review, in history order; k matches the refs r<k>:<id>
  planning-context.json     latest normalized context
  planning-analysis.md      latest analysis
  sources.json              path-free source list (id, title, type, required, status, content_digest)
  guidance.md               copy of the guidance used, if any
```

- `human-needed.json` is also written to the run's runtime directory.
- For `context_not_ready` and `renormalized_context_not_ready` there is no plan; write only what exists (manifest, blockers, context, retrieval).
- Keep emitting `emit_output` as today for compatibility.
- M0 result: `emit_output` prints a `CAO_WORKFLOW_OUTPUT:` line that CAO returns as `output` only in the synchronous `POST /workflows/runs` response.
  `cao workflow run` (default follow mode) never prints it and CAO retains it nowhere (`result`, `status`, `events`, `diagnostics` and the DB all lack it), so the
  files above are the only durable channel. Non-convergence stays `completed` + files.
- Retention: candidates accumulate in Git. No retention policy in this plan; note it in the docs.

### 3.2 Developer guidance

- Input: `guidance_file`, optional, declared as type `string` (a repo-relative or absolute file path). CAO's `path` input type only accepts existing *directories*
  outside blocked system paths, so it cannot carry a file. The workflow resolves it and must reject `..` escapes and symlinks that leave the repository.
  Recommended location `sdlc-records/<ticket>/guidance.md`. An absent optional input is simply missing from `get_inputs()` (use `inputs.get(...)`).
- Format: free Markdown following `docs/templates/developer-guidance.md` (new): sections for Decisions, Constraints, Clarifications, Answers to reviewer findings.
  Findings are addressed by topic or by `r<round>:<id>` (finding IDs like `PLAN-001` are reused across reviews, so a bare ID is ambiguous).
- Prompts: pass the path (not inlined text) to the Planning Analyst, Plan Author and Plan Reviewer, labelled "Developer-provided decisions
  (human-authored, recorded, subordinate to the Planning Context and governance)". Do **not** pass it to the Context Normalizer,
  so normalized requirements stay source-faithful.
- Authority rules (write into `plan-author.md`, `plan-reviewer.md`, `planning-analyst.md` and `policies/governance.md`):
  - Guidance may resolve an ambiguity, choose between options the sources allow, narrow scope or constrain the design.
  - Guidance may not contradict or relax a source requirement (FR/NFR/CON) or governance. A conflict is reported by the reviewer as
    `HUMAN_DECISION_REQUIRED` naming the conflict, so the source gets corrected instead of silently overridden.
  - The plan must cite each applied guidance item in its assumptions/decisions section.
- Binding: `execution-manifest.json` records `guidance_sha256` (and the guidance file is copied next to the published plan), so the human approval covers the guidance that shaped the plan.
- Trust: the file is human-authored and read-only for agents (the write-scope hook already denies `sdlc-records/`). Document this trust decision in `governance.md` and `docs/workflows/planning.md`.

### 3.3 Reviewer history

- Reviewer prompt gains "Previous reviews:" (paths) for round 2 onward and on a warm start.
- New review-JSON field `prior_findings`: `[{"ref": "r2:PLAN-003", "status": "RESOLVED | RESOLVED_BY_GUIDANCE | UNRESOLVED | NOT_APPLICABLE", "note": "..."}]`.
- Deterministic validation in `validate_review` when previous reviews exist:
  - every blocking finding (`PLAN_CHANGE_REQUIRED`, `CONTEXT_RENORMALIZATION_REQUIRED`, `HUMAN_DECISION_REQUIRED`) of the immediately previous review is accounted for;
  - statuses are from the enum; `RESOLVED_BY_GUIDANCE` is allowed only if a guidance file was supplied;
  - the existing status-vs-findings consistency check is unchanged.
- The reviewer stays independent. It must still verify the plan against sources and repository, and may raise new evidence-based findings, marked as new.
  Anchoring is a risk (see section 6). `ADVISORY` findings never block. No blocking finding is ever "accepted" into a `PASS`: a `PASS` means none remains.
- Update `plan-reviewer.md` output schema and rules accordingly.

### 3.4 Warm start

- Input: `resume_from`, optional, declared as type `path` (CAO validates it as an existing directory outside blocked system paths such as `/tmp`): the candidate directory (section 3.1), which the workflow additionally requires to be inside the repository.
- `sources_sha256` is a digest of the sorted `{source_id, status, content_digest}` list. It is deliberately not a hash of `retrieval.json`, which embeds run-specific absolute paths.
- Fail closed unless all of these hold, each with a specific error message:
  - `candidate-manifest.json` state is `NOT_CONVERGED`, the ticket matches, and every recorded file digest matches (tamper check);
  - `baseline_sha` input equals the candidate's `repository_baseline_sha` (so the recorded repository analysis is still valid);
  - fresh deterministic retrieval (no agent) yields the same `sources_sha256` as the candidate (sources unchanged);
  - the stop cause is `review_convergence_limit_reached` or `plan_review_requires_human_decision`. Refuse `context_not_ready` and
    `renormalized_context_not_ready`, which need a cold run;
  - for `plan_review_requires_human_decision`, a guidance file is required and its digest must differ from the candidate's.
- Flow: run retrieval; copy the candidate's context and analysis into the new run's runtime directory (skipping normalization, validation and analysis);
  run a revision author round (previous plan + all prior reviews + guidance); then the normal review loop with its own `max_review_rounds` budget.
- Manifest lineage: `resumed_from` (run id, candidate directory, candidate manifest sha256, prior review rounds) and cumulative `total_review_rounds`.
- If a precondition fails, the error says which one and that a cold run is needed. No automatic fallback in this plan.
- This is different from `cao workflow resume`, which resumes a crashed run from CAO's journal. A warm start is a new run.

### 3.5 Invariants that must not change

- Only a reviewer `PASS` produces `sdlc-records/<ticket>/development-plan.md` and an `AWAITING_HUMAN_APPROVAL` manifest.
- Human approval is recorded only by `approve_plan.py`, bound to the plan SHA-256 and the baseline SHA.
- Planning agents remain read-only apart from their instructed answer file under `.agentic-sdlc/runtime/`.
- Delivery is untouched. `approve_plan.py` and Delivery take only a ticket ID and never look under `candidates/`.

## 4. Files to change

| File | Change |
|---|---|
| `.agentic-sdlc/cao/sdlc_workflows/planning.py` | `INPUTS` (literal, the bundler requires it): add `guidance_file`, `resume_from`. Guidance/resume validation, candidate snapshot writer, prompt builders (guidance, prior reviews), `validate_review` (`prior_findings`), warm-start branch in `main()`, manifest fields. |
| `.agentic-sdlc/cao/sdlc_workflows/artifacts.py` | Only if a helper (e.g. digest of a file set) is shared. Constants keep exactly one owner. |
| `.agentic-sdlc/cao/profiles/{plan-reviewer,plan-author,planning-analyst}.md` | Guidance authority rules; reviewer history and the `prior_findings` schema. Profiles must be reinstalled afterwards. |
| `.agentic-sdlc/policies/governance.md`, `.agentic-sdlc/contracts/planning-workflow.md` | Guidance trust model, strict `PASS`, `NOT_CONVERGED` candidate state, warm start. These files are read by the workflow at runtime. |
| `docs/workflows/planning.md`, `docs/architecture.md`, `docs/README.md` | Usage, the non-convergence procedure, layout (`sdlc-records/<ticket>/candidates/`), link to this plan. |
| `docs/templates/developer-guidance.md` | New template. |
| `tests/test_dev_plan.py`, `tests/test_workflow_integration.py`, `tests/test_approve_plan.py`, `tests/test_workflow_packaging.py` | See section 5. |

## 5. Implementation sequence and verification

Follow the "small live-verified increments" rule. Each milestone ends green in both test modes
(`python3 -m unittest discover -s tests` and with `SDLC_TEST_SOURCE=1`; 140 tests pass today) and, where noted, with a live run.

| Milestone | Work | Verification |
|---|---|---|
| M0 spike | (a) Confirm the bundler and CAO input scanner accept optional inputs without a default (`INPUTS` entry with `required: False` and no `default`) via `install_workflow.py dev_plan --validate-only`. (b) Find where `emit_output` goes in the installed CAO package. (c) Save run 18's `planning/`, `context/` and `analysis/` directories as a test fixture before any runtime cleanup. | Notes added to this plan. |
| M1 candidate evidence | 3.1 for all four stop causes. | Unit tests per stop cause (force non-convergence through the fake-agent integration harness). **Live:** cold run with `max_review_rounds=1` on PAY-DEMO-001 (cheap, forces non-convergence) then inspect `sdlc-records/PAY-DEMO-001/candidates/<run-id>/`. |
| M2 guidance | 3.2: input validation, prompts, profile text, manifest binding, template, docs. | Unit tests: path safety (outside repo, symlink escape, missing), prompts contain the guidance path for analyst/author/reviewer and not for the normalizer, digest recorded. |
| M3 reviewer history | 3.3. | Unit tests for `prior_findings` coverage/enum/`RESOLVED_BY_GUIDANCE` rules and prompt contents. |
| M4 warm start | 3.4. | Unit tests for the validity matrix (baseline mismatch, source digest mismatch, tampered file, wrong ticket, wrong stop cause, missing or unchanged guidance when required). Integration: fake agents, cold non-convergence, guidance, warm start, `PASS`, publish with lineage and `guidance_sha256`, then `approve_plan.py`. |
| M5 docs, install, live end to end | Docs and governance text. Reinstall the workflow and the three profiles (see section 7). | **Live:** cold run (`max_review_rounds=1`) leaves a candidate, then a warm start with a `guidance.md` reaches `PASS` and publishes to **`sdlc-records/PAY-DEMO-001/`**. This also finally verifies the records-location change live. Stop at `AWAITING_HUMAN_APPROVAL`. The human runs `approve_plan.py`. |

Also add a test that `approve_plan.py` refuses a ticket that has only `candidates/` and no `development-plan.md`.

### 5a. M0 findings (2026-09-20, live against CAO 2.5.0)

- Optional inputs with no `default` are accepted by CAO's `INPUTS` scanner (`required` and `default` are both optional) and a run without them completes; absent keys are not present in `get_inputs()`.
- `path` inputs are validated as existing directories and blocked system paths (e.g. `/tmp`) are refused, so a file cannot use type `path`; use `string` and validate in code.
- `emit_output` is returned only by the synchronous `POST /workflows/runs` (used by the CLI's blocking mode) and is not retained. Whether `cao workflow run --wait --json` prints it was not verified.
- Run 18's artifacts were saved as a test fixture in `tests/fixtures/plan-run-18-nonconverged/` (context, analysis, last plan, all three reviews, raw sources). Its README says which parts contain machine-specific paths. Runtime evidence for runs 17 and 18 is no longer the only copy.
- `cao workflow run ... --wait --json` prints the emitted `output` payload (verified live on run 20), so a developer can see the blockers at the CLI by using that form.
- A throwaway probe workflow was registered, run and deleted; the CAO registry is back to `sdlc_dev_plan`, `sdlc_deliver`, `source_review`.

### 5b. M1 live result (2026-09-20, run `plan-PAY-DEMO-001-20`, `max_review_rounds=1`)

- Stopped with `review_convergence_limit_reached`. `sdlc-records/PAY-DEMO-001/candidates/plan-PAY-DEMO-001-20/` holds the manifest (state `NOT_CONVERGED`, all 6 file digests verified), `human-needed.json` (4 blocking findings), `candidate-plan.md`, `planning-analysis.md`, `planning-context.json`, `sources.json` and `reviews/review-r1-c1.json`.
- No `development-plan.md` or `execution-manifest.json` was published, and `approve_plan.py` refused with "Reviewed plan or execution manifest is missing".
- This candidate is the intended input for the M4 warm-start live test. Keep it until then.

### 5c. M2 implementation notes (2026-09-20)

- `guidance_file` is a `string` input validated by `resolve_guidance()` (regular UTF-8 file, 1 byte to 64 KiB, inside the repo, outside `.agentic-sdlc/runtime/`, symlink escapes refused) and frozen per run by `freeze_guidance()` into `runtime/<ticket>/<run-id>/guidance/developer-guidance.md`.
- Wired into the analyst, author (all rounds) and reviewer prompts via `_guidance_lines()`; prompts are byte-identical when there is no guidance. Not passed to the normalizer.
- `execution-manifest.json` and the workflow output carry `guidance_sha256`; the exact file is published as `sdlc-records/<ticket>/plan-guidance.md`; candidate manifests carry it too.
- `approve_plan.py` now verifies `plan-guidance.md` against the manifest digest (and refuses a stray guidance file when the manifest records none), and stores `guidance_sha256` in the approval record. Old manifests without the field still work.
- Governance invariants 16 (developer guidance) and 17 (review gate) were added; profile and contract text updated; docs and `docs/templates/developer-guidance.md` written. The three planning profiles must be reinstalled before the next live run.
- Tests: `tests/test_planning_guidance.py` (6 tests, mutation-checked).

### 5d. M3 implementation notes (2026-09-20)

- `validate_review(review, required_refs=None, guidance_supplied=False)` now also checks `prior_findings` (`_validate_prior_findings`): every required ref exactly once, no unknown or duplicate refs, statuses from the enum, `RESOLVED_BY_GUIDANCE` only with guidance, `PASS` refused while any is `UNRESOLVED`, and `prior_findings` must be empty when there is nothing to account for. Old single-argument calls still work.
- Refs are `r<k>:<id>` where k is the review's 1-based position in the history list (`review_paths`). M4 must pre-load the candidate's reviews into that list so refs stay unique across a warm start; the candidate snapshot names reviews `review-r<round>-c<V>.json`, which will collide between the candidate's rounds and the new run's rounds, so M4 has to rename or index them.
- `build_reviewer_prompt` takes `previous_reviews` and `required_refs`; the loop passes the latest review's blocking refs only.
- The reviewer profile documents the rules and the schema; the profile must be reinstalled before the next live run.
- Existing fake reviewers in tests now supply `prior_findings` through `harness.prior_findings_for(prompt)`. Tests: `tests/test_planning_reviewer_history.py` (4 tests, mutation-checked).

### 5e. M4 implementation notes (2026-09-20)

- `resume_from` is declared type `path`; `load_candidate()` does the real validation (candidate directory of this ticket, `NOT_CONVERGED`, stop cause allowed, every recorded file present, safe and digest-matching, baseline equal, guidance rules) before any agent runs; the workflow then compares `sources_sha256` after its deterministic retrieval.
- The candidate's reviews are copied into `runtime/<ticket>/<run-id>/planning/history/` named by history position (`01-review-r1-c1.json`, ...), together with `previous-plan.md`. Candidate snapshots now name reviews `<k>-review-r<N>-c<V>.json` (k = history position = the ref index), so history keeps growing across consecutive warm starts without name collisions.
- The warm branch is `if warm is None: ... else: ...` around steps 2 to 5 of `main()`; the first author step is `plan-author-warm-c<V>`, and the review loop is unchanged (own `max_review_rounds` budget, step ids `plan-review-r<n>-c<V>`).
- Published manifests gain `resumed_from` and `total_review_rounds`; candidate manifests gain `resumed_from`.
- Tests: `tests/test_planning_warm_start.py` (5 tests including a 10-case fail-closed matrix; five guards mutation-checked).
- A `plan-author.md` profile note was added; all three planning profiles plus the reviewer and the `dev_plan` bundle must be reinstalled before the live run.

### 5f. M5 live end-to-end result (2026-09-20)

- Reinstalled the `dev_plan` bundle (validated by CAO's own scanner: `guidance_file` string and `resume_from` path inputs accepted) and the `planning-analyst`, `plan-author` and `plan-reviewer` profiles; installed copies were byte-identical to the repo.
- Cold run `plan-PAY-DEMO-001-21` (`max_review_rounds=1`, `--wait --json`): stopped with `review_convergence_limit_reached`; candidate in `sdlc-records/PAY-DEMO-001/candidates/plan-PAY-DEMO-001-21/` with the index-prefixed review `reviews/01-review-r1-c1.json`. One blocking finding: `record_if_new` maps every `sqlite3.IntegrityError` (including NOT NULL on `payment_id`) to "duplicate".
- Synthetic guidance (`sdlc-records/PAY-DEMO-001/guidance.md`, labelled as an assistant-written test fixture, not a human decision) decided that only a PRIMARY KEY violation is a duplicate.
- Warm run `plan-PAY-DEMO-001-22` (`resume_from` = that candidate, `max_review_rounds=3`): only `plan-author-warm-c1` and `plan-review-r1-c1` ran (no normalizer, validation or analysis); the reviewer returned `PASS` in one round with two LOW advisories and `prior_findings` [('r1:PLAN-001', 'RESOLVED')] for the earlier finding. The plan applies and cites D1.
- Published to `sdlc-records/PAY-DEMO-001/` (this also verifies the records-location change live): `development-plan.md`, `plan-review.json`, `execution-manifest.json` (`resumed_from`, `guidance_sha256`, `total_review_rounds: 2`), `plan-guidance.md`. Every digest was checked against its file. The manifest state is `AWAITING_HUMAN_APPROVAL`; no approval was recorded.
- Comparison: the same ticket needed 3 to 4 full review rounds without guidance in runs 17 and 18. One caveat: a single warm run is one data point, and the guidance was written with knowledge of the finding, so it says nothing yet about how often guidance converges a real ticket.

## 6. Risks and open questions

- **Anchoring:** giving the reviewer prior findings could bias it toward its earlier verdict. Mitigation: it must still audit independently and may add new evidence-based findings. Reassess after the live tests.
- **Finding identity:** IDs repeat across reviews, so references use `r<round>:<id>`. If reviewers produce inconsistent references, tighten validation.
- **Candidate volume in Git:** every non-converged run adds a directory. Decide later on pruning, or on committing candidates only on request.
- **Warm start when sources or baseline changed** fails closed. A partial reuse (re-run analysis only) is a possible later extension.
- **Guidance quality:** a wrong human decision goes into the approved plan. The plan cites it, the manifest binds its digest, and the human still approves the plan.

## 7. State to resume from and preconditions

- Branch `feature/slim-agentic-sdlc`, commit `a806a98` (not merged to `main`). It moved docs, tools, examples and records out of `.agentic-sdlc/`.
  Records now go to `sdlc-records/<ticket>/`.
- The CAO-installed workflows (`sdlc_dev_plan`, `sdlc_deliver`) and profiles were reinstalled from that commit on 2026-09-19; `.bak` copies of the previous workflows sit beside them.
  The CAO registry is machine-wide, so this affected every project on the laptop.
- Live verification of the `sdlc-records/` location is **still pending**: run 18 did not converge and run 19 was cancelled.
- The runtime evidence for runs 17 and 18 is under `.agentic-sdlc/runtime/PAY-DEMO-001/`. Keep it until M0c is done.
- A Delivery run for a ticket planned on this branch needs the baseline to be an ancestor of `base_branch`, so merge the branch (or pass the feature branch as `base_branch`) first.
- Untracked and left out of the commit: `tmux-*.log`, `.gitignore.additions`.
- Before installing any change: `python3 .agentic-sdlc/cao/install_workflow.py dev_plan --validate-only`, then
  `bash .agentic-sdlc/cao/workflows/install.sh "$PWD"`, then `cao install .agentic-sdlc/cao/profiles/<profile>.md` for each changed profile, and use new run IDs.
- Useful commands: `cao workflow run|status|result|wait|cancel|runs <run-id>`. Do not detect completion with `pgrep -f` (it matches its own shell).

## Appendix: run 18 round-3 findings (seed for the first guidance test)

Review `plan-PAY-DEMO-001-18/planning/review-r3-c1.json`, all LOW:

- `PLAN-001` TESTING, `PLAN_CHANGE_REQUIRED`: T4's transaction-lifetime test "pins the dependency" on `close()` ending the implicit SQLite transaction, but as specified it does not.
- `PLAN-002` TESTING, `PLAN_CHANGE_REQUIRED`: T8's `git diff --stat` cannot confirm the changed file set because the new test file is untracked.
- `PLAN-003` REQUIREMENTS, `ADVISORY`: HD-6 (release the claim on any `BaseException`) is listed as non-blocking though it changes concurrency semantics.
- `PLAN-004` TESTING, `ADVISORY`: tests that block on a `threading.Event` can leave a worker thread holding an open connection if an assertion fails first.

Run 18's reviewer summary also says the context matches the three raw sources and that the plan covers AC-1..AC-3, FR-1..FR-7, NFR-1..NFR-3 and CON-1..CON-7.
