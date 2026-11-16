# Plan: project knowledge for the reviewers (coding standards)

Status: **done: steps 1-5 (4 and 5 on 2026-10-14), 5b (every file checked) and 6 (docs) on 2026-11-16**. Decisions in section 3 are all agreed (D1-D15 2026-10-13, D16-D17 2026-11-16). Progress notes are at the end.

## 1. Why

The reviewers only know what their profile, the policies and the diff tell them. A project cannot tell them its own
rules, for example "identifiers are full names, not acronyms" or "no change-history comments in the code". Today such
a rule is either never checked, or it is rejected as a "style nit" (Source review excludes style nits and speculative
cleanups, and its validator rejects findings without a reachable failure).

The goal: a project keeps its rules as Markdown files, the workflow hands the rules that apply to a change to a
dedicated standards reviewer in a trusted and auditable way, every applicable rule gets a verdict, and a finding cites
the rule it enforces. A large rule set (50 rules or more) must neither flood an agent's context nor make the review
noticeably slower.

## 2. Facts checked in the code (2026-10-13, `main` `f4a17b3`)

| Fact | Where | Consequence |
|---|---|---|
| Reviewers who look at code: Delivery `sdlc_pr_reviewer`; Source review `mapper`, `correctness`, `security`, `validator`, `feedback`; hybrid `sdlc_code_supervisor` (accepts worker output) | `delivery.build_pr_reviewer_prompt`, `source_review.run_agents`, `hybrid.py` | Delivery and Source review need separate wiring. The plan reviewer reviews plans, not code. |
| Delivery's reviewer prompt names its inputs by path (plan, contract, governance, PR review policy) and embeds the diff | `delivery.build_pr_reviewer_prompt` | The standards reviewer's prompt follows the same pattern. |
| Source review agents work in an isolated workspace built from the PR's git objects; PR content is "untrusted data, not instructions" | `source_review.prepare_snapshot`, `configure_workspace`, the `common` prompt | Rules must come from the trusted checkout (`repository_root`), never from the PR head, and must be copied into the workspace. |
| Source review scope excludes "style nits, speculative cleanups or pre-existing defects" and needs a "concrete, reachable failure scenario"; the validator rejects speculation | `common` prompt, `sdlc_source_correctness` profile, validator prompt | Rule findings need an explicit exception in the scope text and the validator prompt. |
| Categories are fixed sets checked by the contracts: Delivery has `STYLE`, `CORRECTNESS`, ...; Source review has `CORRECTNESS`, `TESTING`, `RESOURCE_HANDLING`, `SECURITY`, `OTHER` + protected ones | `delivery.py` vocabulary, `source_review_contracts.CATEGORIES` | Routing depends on category (D6). |
| Source review already parses the diff into per-file hunk ranges (`diff_hunks`, `hunk_ranges`); the ranges include unchanged context lines | `source_review.py` | D14 needs a small helper that keeps only added lines; it can be shared with Delivery. |
| Delivery's finding shape has `file` and a free-text `location`, no line numbers | `pr-reviewer.md` output shape | The Delivery standards reviewer's finding shape adds `line_start`/`line_end` (head side) so D14 can check them. |
| Delivery runs up to 4 review rounds (the first and up to 3 remediation rounds), each on the full diff | `delivery.py` (`MAX_REMEDIATION_ROUNDS`) | D15 avoids repeating the standards review for files a round did not change. |
| Source review sets `coverage_status` to `INCOMPLETE` when any coverage gap exists | `source_review.run_agents` | An unassessed rule (D7) can reuse this. |
| Source review runs its agents one by one, "to keep CAO replay ordering stable and avoid claiming shared server capacity" | comment in `source_review.run_agents` | D10 changes this; replay stays stable through fixed step ids, capacity through a cap. |
| Hybrid Delivery already runs agent calls concurrently: `ThreadPoolExecutor`, at most `MAX_PARALLEL_WORKERS = 4`, fixed `step_id` per call; live run: implementation 4m16s against 7m26s sequential | `hybrid.py`, `agentic-sdlc-docs/verification/parallel-workers-live.md` | The pattern is proven; reviewers are read-only, so they need no worktrees, merges or conflict handling. |
| CAO `step_id` is at most 64 characters | parallel workers plan (task id limit 40) | Batch step ids stay short (`source-standards-b1`). |
| CAO input defaults must be literals | packaging test on `INPUTS` | `review_max_parallel` needs a literal default. |
| The project file allows only `version`, `source_roots`, `write_profiles`, `verification` | `source_config.validate_project_file` **and the hook's copy** in `.claude/hooks/restrict-write-scope.py`; parity in `tests/test_source_config.py` | A new top-level key makes the hook treat the file as invalid (every source write denied) unless the hook changes in the same commit. |
| `FORBIDDEN_ROOTS` lists the SDLC's own folders; a source root may not overlap them | `source_config.FORBIDDEN_ROOTS` and the hook copy | The rules folder must be added there, or a project could make it a source root and the implementer/remediator could weaken the rules they are reviewed against. |
| `READ_ONLY_PROFILES` names the read-only profiles; every `sdlc_source_*` profile is read-only by prefix | `source_config.py` and the hook copy | The new Delivery standards reviewer is added to the set (both copies); the Source review one is covered by the prefix. |
| `agentic-sdlc-pr-review-kb/` exists at the repo root (created 2026-10-11); now holds the sample `code-readability.md` | repo root | Home of the rule files (D2). |
| Installed workflows and profiles are frozen at install, profiles machine-wide | `build-and-install.md` | Each step ends with a reinstall before live verification. |
| One rule in the agreed format is about 2 KB (500-600 tokens); 50 rules are about 100 KB (25-30k tokens) | sample file: 4.2 KB for 2 rules | Giving every reviewer every rule is not viable (section 4.2). |

## 3. Decisions

| # | Question | Recommendation |
|---|---|---|
| D1 | Which agents receive the rules? | **Agreed 2026-10-13.** **A dedicated standards reviewer** in each workflow (D5); the correctness, security and PR reviewers do not get them and stay focused. Downstream agents get only what they need: the Source review validator and the remediators the rules cited by the findings they handle. **Authors** in a later step: implementer, code supervisor, remediator and source remediator get the index plus the rules matching the files they will change. Not the plan reviewer. |
| D2 | Where are they declared, and where do the files live? | **Agreed 2026-10-13.** A new project-file key `review_knowledge` (D3). Files must live under **`agentic-sdlc-pr-review-kb/`**, which is added to `FORBIDDEN_ROOTS` (workflow and hook) so no agent can write it. |
| D3 | Declaration shape and limits | **Agreed 2026-10-13.** `review_knowledge` is an object: `{"files": ["agentic-sdlc-pr-review-kb/code-readability.md"], "batch_max_rules": 15, "batch_max_kb": 32, "max_files": 32, "max_rules": 200, "max_rule_kb": 8}`. Only `files` is required (an explicit list, so a draft file in the folder is never used by accident). The other keys are optional with these defaults, all validated against fixed hard maximums: `batch_max_rules` 1-50, `batch_max_kb` 4-128, `max_files` 1-128, `max_rules` 1-1000, `max_rule_kb` 1-32. There is no total size limit, because a large set costs more batches, not a bigger prompt (D8). An invalid value or file stops the run before any agent. |
| D4 | Rule file format | **Agreed 2026-10-13**, sample `agentic-sdlc-pr-review-kb/code-readability.md`. Each rule is a `##` heading `RULEID - [MANDATORY\|NICE-TO-HAVE] Domain. Title`, followed by the `###` sections `Applicable files` (a list of backticked globs), `Rule description`, `Why we have the rule`, `Examples: bad`, `Examples: good`. Ids match `[A-Z][A-Z0-9]*-[0-9]+` and are unique across all files. The workflow parses id, level, domain, title and globs and rejects a rule with a missing section, so a citation can be checked deterministically. |
| D5 | Who reviews against the rules? | **Agreed 2026-10-13.** A new standards reviewer per workflow: `sdlc_source_standards` (Source review) and `sdlc_standards_reviewer` (Delivery). It gets the diff, the index of applicable rules (D7) and reads full rule texts on demand. |
| D6 | How does a finding cite a rule? | **Agreed 2026-10-13.** **No new routing.** A rule finding has a mandatory field `standard_ref` (a rule id); the contract rejects an unknown id or one whose `Applicable files` do not cover the finding's file. Delivery already has `STYLE` and `DOCUMENTATION` for the sample rules; Source review has no fitting category (only `OTHER`), so it gains **`MAINTAINABILITY`** (not protected), allowed only with a `standard_ref`. A protected or HIGH finding still goes to a human. **Business-sensitive code goes to a human (confirmed 2026-10-14):** in Source review every finding in a file the mapper marks with a sensitive boundary does already; Delivery has no such map, so a rule finding in a file the PR reviewer flags with a protected category in the same round becomes `DEVELOPER_REQUIRED`. |
| D7 | Every applicable rule gets a verdict | **Agreed 2026-10-13.** The standards reviewer returns `rules: [{"id", "status": "VIOLATED\|NOT_VIOLATED\|NOT_ASSESSED", "finding_ids"}]` alongside its findings. The contract requires each rule of its batch exactly once, at least one finding for `VIOLATED` and none otherwise. `NOT_ASSESSED` becomes a coverage gap and the run continues (Source review: `coverage_status` `INCOMPLETE`; Delivery: listed in the manifest and the human review brief). |
| D8 | How large is one standards call? | **Agreed 2026-10-13.** Python selects the applicable rules (rule globs against changed files) and splits them into batches of at most `batch_max_rules` rules and `batch_max_kb` KB of rule text (D3; defaults 15 and 32, about 8k tokens). Batches are deterministic (rules sorted by id, filled up to the budget, grouped by domain only when that does not unbalance them), so the same input gives the same steps. |
| D9 | Severity of a rule finding, and does it need a reachable failure? | **Agreed 2026-10-13, revised 2026-10-14.** A finding with `standard_ref` may describe the violated rule instead of a reachable failure, only for lines the change adds or edits (never pre-existing code). **The reviewer's severity stands; nothing caps it.** Prompts and profiles give the normal level (LOW or MEDIUM for a `MANDATORY` rule, LOW for a `NICE-TO-HAVE` rule) and allow a higher severity when the violation also causes a concrete problem (a reachable failure, a security exposure), stated in the consequence. Without `standard_ref` today's rules apply unchanged. |
| D10 | Do reviewers run in parallel? | **Agreed 2026-10-13.** Yes, behind a workflow input `review_max_parallel` (default 4, range 1-4; 1 keeps today's sequential order). Source review: after the mapper, correctness, security and every standards batch run together; then the validator. Delivery: in each review round, the PR reviewer and the standards batches run together. Fixed step ids keep CAO replay stable. One failed call fails the stage as today, with its own evidence folder; progress lines carry role and batch. |
| D11 | What does the level change in routing? | **Agreed 2026-10-13, revised 2026-10-14.** A `NICE-TO-HAVE` finding is a suggestion (never `AUTO_FIX`, never blocking) unless it is rated HIGH: that exception is routed like any other finding, so to a human. A `MANDATORY` finding is routed like any other finding of its category and severity. |
| D12 | Trust and audit | **Agreed 2026-10-13.** The workflow reads the files from the trusted checkout (Delivery: the repo the run starts from; Source review: `repository_root`, not the PR), copies them into the run's runtime folder (Source review: into the workspace), and records per file `path` and `sha256`, the applicable and skipped rule ids, the batches and the verdicts in the delivery manifest / `code-review.json`. A PR that edits the rules does not change its own review. |
| D13 | Without `review_knowledge` | **Agreed 2026-10-13.** No standards reviewer runs; behaviour and prompts are exactly as today, except that D10's parallelism also applies to correctness and security (tests prove the rest is unchanged). |
| D14 | Rule findings outside changed lines | **Agreed 2026-10-13, revised 2026-10-14.** Python checks every standards finding: its line range (head side) must overlap a line the change **adds** (not just a context line of a hunk). A finding that does not is **kept as a suggestion** with the reviewer's severity, never `AUTO_FIX`, never blocking, marked `outside_changed_lines` (Source review) or with a `suggestion_reason` (Delivery). It skips the Source review validator and is never sent to a remediator. |
| D15 | Later Delivery rounds | **Agreed 2026-10-13.** In round n > 1, a rule is reviewed again only if one of its covered files changed since the previous round's reviewed commit (`git diff --name-only`); otherwise its verdict and open findings carry forward, marked with the round they come from. The PR reviewer still reviews the full diff every round. |
| D16 | Every file is checked | **Agreed 2026-11-16** (after a live run missed PY-1 occurrences in a batch of 8 rules). A verdict is given **per rule and file**: `rules: [{"id", "files": [{"path", "status", "finding_ids"}]}]`. Python requires one entry for every file a rule covers in the batch; `VIOLATED` lists exactly the findings citing that rule in that file. A missing entry gets the contract-repair turn; if the repaired answer still lacks it, the last attempt records it as `NOT_ASSESSED` (or `VIOLATED` when findings cite it) with a coverage gap, instead of failing the run. A rule's overall verdict is `VIOLATED` in any file, else `NOT_ASSESSED` in any file, else `NOT_VIOLATED`; the not-assessed list names each rule and file. This proves no file was skipped silently; it cannot prove a `NOT_VIOLATED` is right. Prompts and profiles also ask for every occurrence, not only the first. |
| D17 | Files per batch | **Agreed 2026-11-16.** New optional setting `batch_max_files` (default 5, bounds 1-50). The rules are grouped as before (D8); each group's covered files (sorted) are split into the fewest even chunks of at most that many files, and a batch is one group restricted to one chunk, so every (rule, file) pair is reviewed exactly once. The reviewer sees only its batch's part of the diff (Delivery: in the prompt; Source review: `knowledge/diff-b<k>.patch`). More calls, smaller context per call. Records list each batch as `{"rules", "files"}`. |

## 4. Design

### 4.1 Configuration (D2-D4, D13)

- `source_config.py`: `PROJECT_KEYS` gains `review_knowledge`. The object itself (keys, D3 ranges, each path in
  `files` a normalized `.md` path inside `agentic-sdlc-pr-review-kb/` that exists and does not resolve out of it) is
  checked by `review_knowledge.validate_knowledge_settings()`, since the hook never needs it. `FORBIDDEN_ROOTS` gains `agentic-sdlc-pr-review-kb`; `READ_ONLY_PROFILES` gains
  `sdlc_standards_reviewer`.
- `.claude/hooks/restrict-write-scope.py`: same key, forbidden root and read-only profile in its standalone copy, and
  the folder in `DENY_ALWAYS_ROOTS` (like the records); the
  parity test in `tests/test_source_config.py` covers both. The hook does not inspect the entries.

### 4.2 Keeping the context small (D1, D7, D8)

A new module `review_knowledge.py`, pure Python:

- `load_review_knowledge(repo)`: parses every declared file into rules (id, level, domain, title, globs, full text)
  with `sha256` per file; enforces D3 and D4.
- `select_rules(knowledge, changed_files)`: the applicable rules, each with the changed files it covers, and the
  skipped rules. No agent sees a rule that matches none of the changed files.
- `build_batches(rules)`: D8.
- `stage_rules(batch, destination)`: writes one file per rule (`knowledge/rules/NAMING-1.md`) and the batch index
  `knowledge/index-b<k>.md`: one line per rule (id, level, domain, title, covered changed files), about 40 tokens a
  line. The prompt contains only the index; the reviewer reads a rule's full text when it suspects a violation or
  needs the examples. `knowledge/index.json` keeps the full selection for audit (D12).

Index only is decided (2026-10-13): full rule texts are not put in the prompt; the reviewer reads them when needed.
Prompt order is fixed so parallel calls share the longest common start (possible prompt-cache reuse): shared
instructions first, then the diff, and the batch-specific index last.

Context per call: the diff plus at most 15 index lines and the rule texts the agent chooses to read (at most 32 KB).
50 rules cost about four calls, not a 100 KB prompt; the validator and remediators get only cited rules.

### 4.3 Parallel reviewers (D10)

- A shared helper `run_parallel_steps(steps, max_parallel)` in `runtime.py`, wrapping `_run_json_contract_step` in a
  `ThreadPoolExecutor` like `hybrid.run_hybrid`; it returns results in step order regardless of finish order, so the
  merged report is deterministic.
- Step ids: `source-correctness`, `source-security`, `source-standards-b<k>`; Delivery `pr-review-r<n>`,
  `standards-review-r<n>-b<k>` (all under 64 characters).
- `review_max_parallel` input in both workflows, validated like `hybrid_max_parallel`, recorded in the manifest /
  `code-review.json`.

### 4.4 Source review (D5-D12)

- `main` loads knowledge from `repository_root` before `prepare_snapshot`; after the snapshot, Python selects and
  batches the rules against `changed_files` and stages them into `workspace/knowledge/` (the agents' only readable
  copy).
- `common` prompt: rules in `knowledge/` are trusted project rules (unlike the PR source).
- New profile `sdlc_source_standards` and prompt: review only the rules in its index, D9 scope, the D7 verdict list.
  `FINDING_SHAPE` gains `standard_ref`.
- Correctness, security and the standards batches run in parallel (D10); their candidates merge in step order.
- `source_review_contracts`: `standards_contract` (D6, D7, D9); the adjudication contract checks `standard_ref`.
  The validator prompt names the cited rules' files and accepts a cited, applicable violation on changed lines
  without a reachable failure.
- D14: after the standards batches, Python marks findings outside added lines (shared helper `added_lines(diff)` next
  to `diff_hunks`) as suggestions; only the others go to the validator.
- `NOT_ASSESSED` rules join `coverage_gaps`; `comments.md` and the review draft show the rule id next to the finding;
  `code-review.json` records D12.
- Source remediation receives findings with `standard_ref`; its prompt names the cited rules' files (step 5).

### 4.5 Delivery (D5-D12)

- Load before any agent runs (with the other settings checks). Each review round selects rules against the round's
  changed files, stages them into `runtime/<ticket>/<run>/knowledge/r<n>/` and runs the PR reviewer and the standards
  batches in parallel (D10).
- New profile `sdlc_standards_reviewer` (read-only, like `sdlc_pr_reviewer`); its finding shape adds `line_start` and
  `line_end` on the head side.
- D14: findings outside added lines of the round's diff become suggestions before routing.
- D15: from round 2, only rules with a changed covered file are batched; the rest carry forward. The manifest records
  per rule the round its verdict comes from.
- `classify_pr_review` gains the D6/D7/D9/D11 checks for standards findings; findings of both reviewers merge before
  routing. The manifest records D12; `NOT_ASSESSED` rules appear in the human review brief.
- Resume: knowledge is reloaded from the current checkout; the manifest notes when digests changed since the earlier
  run.

### 4.6 Authors (D1, later step)

- Implementer and code supervisor prompts (both modes) and the remediators get the index of rules matching the files
  their task names (the plan's file list; remediators: the finding's file) and read full rules on demand, with
  "follow these project standards" wording. No new validation.

## 5. Steps

Each step: unit tests green, reinstall the affected workflows and profiles, live-verify, commit on the feature branch
`feature/reviewer-knowledge`.

1. **Configuration and parsing.** Section 4.1 and the loading, selection and batching parts of 4.2, with tests
   (parsing the sample file; rejecting a missing section, a bad level, a duplicate id; selection by globs; batch
   determinism and budgets). Add the entry for `agentic-sdlc-pr-review-kb/code-readability.md` (`NAMING-1`,
   `COMMENTS-1`) to `agentic-sdlc-project.json`. Live check: the hook still allows implementer writes under `app/`
   and denies writes to `agentic-sdlc-pr-review-kb/`.
2. **Parallel Source review reviewers.** Section 4.3 for Source review only: correctness and security in parallel.
   Useful on its own. Live: the same local fixture with `review_max_parallel` 1 and 4; same findings shape, step
   evidence for both, and the elapsed times recorded.
3. **Source review standards reviewer.** Section 4.4. Live: a local fixture (`source_repository`, base/head SHAs)
   whose head adds a correct function with abbreviated names (`calc_fee(amt, cur)`) and a `# Rule removed on 20-Jan`
   comment. Expect findings citing `NAMING-1` and `COMMENTS-1` that survive the validator, and a verdict for both
   rules; without `review_knowledge` no standards step runs. Unit tests cover D14 (a finding on a context line or an
   untouched line becomes a non-blocking suggestion that skips the validator). A head commit that edits `code-readability.md` does not
   change the rules used. Then a generated set of 50 rules (most not matching the fixture's files): check the skipped
   rules, the batch count, parallel timings and that every applicable rule has a verdict.
4. **Delivery standards reviewer.** Section 4.5. Live: a Delivery run (demo ticket) where the implementer is steered
   to abbreviated names (authors do not see the rules yet); the standards review cites `NAMING-1`, the PR reviewer ran
   in parallel, the manifest has the digests and verdicts. If the run needs a remediation round, check D15: only rules
   whose files the remediator changed are reviewed again (unit tests cover it either way).
5. **Authors.** Section 4.6. Live: rerun step 4's scenario; the implementer uses full names and the review has no
   `NAMING-1` finding.
5b. **Every file checked (D16, D17).** Live: a Source review fixture with planted violations over 7 files (known
   count, so recall is measured) and a Delivery run on PAY-DEMO-001 checking per-file verdicts, batches and the brief.
6. **Docs.** `workflows/source-review.md`, `workflows/delivery.md`, `workflows/hybrid-delivery.md`,
   `reference/agent-profiles.md` (two new profiles), `reference/write-scope-hook.md`, `architecture.md`,
   `build-and-install.md` (project-file keys, new profiles), `.agentic-sdlc/policies/pr-review.md` and
   `agentic-sdlc-docs/policies/source-review.md` (rule findings, D9, D11), and a short guide to writing rule files,
   following the workflow-docs conventions.

## 6. Parked (decided 2026-10-13)

- **Benchmark and cost measurement.** Not before there is a realistic rule set and a representative PR; a synthetic
  benchmark would not give relevant numbers. Note: CAO defines token-usage constants but records nothing; Claude Code
  session transcripts hold the usage when this is picked up.
- **Model and effort per role.** CAO profiles already support `model:` and `claudeConfig: {"effort": ...}`; none of
  ours set them. To be solved with the separate feature "select provider, model and effort per profile, configurable
  per project", not in this plan.
- **Batching by file instead of by rule** (cheaper for a large PR with few rules). Revisit with real PRs.

## 7. Progress notes

- **Step 1 done (2026-10-13).** `review_knowledge.py` (settings, rule parsing, selection with `PurePosixPath.full_match`
  globs, balanced batching, staging), the key/folder/profile in `source_config.py` and the hook, the sample file
  declared in `agentic-sdlc-project.json`. Tests: `tests/test_review_knowledge.py` (new), parity cases in
  `test_source_config.py`, hook cases in `test_restrict_write_scope.py`; two helpers that list the project keys
  (`test_workflow_integration.edit_config`, `test_hybrid`) updated. Full suite 340 OK. A mutation check (rules folder
  and profile removed from the hook copy only) fails the parity test, as it should.
  Live: a real `sdlc_implementer` terminal created through CAO's API, hook run against its id with the real project
  file: `app/payment_service/payment_service.py`, `app/tests/test_new.py` and the answer path allowed;
  `agentic-sdlc-pr-review-kb/code-readability.md`, `agentic-sdlc-pr-review-kb/new-rules.md` and
  `agentic-sdlc-project.json` denied. Terminal deleted.
  Reinstalled `sdlc_dev_plan`, `sdlc_deliver` and `source_remediate` from this branch (the old bundles would refuse a
  project file with `review_knowledge`; the new ones accept it with or without the key) and checked that each loads the
  real project file. `source_review` was not reinstalled: it does not read the project file yet, and its installer
  requires the upgrade procedure (step 3).
- **Step 2 done (2026-10-13).** `runtime.run_parallel_steps` (results in call order, cap `MAX_PARALLEL_REVIEWS = 4`,
  every step awaited, the first failure in call order raised; 1 = sequential on the calling thread) and the Source
  review input `review_max_parallel` (default 4, validated before any work, recorded in `code-review.json`);
  correctness and security run through it. Tests: overlap proven with a barrier, fixed merge order when security
  finishes first, sequential order with 1, a failing reviewer fails the review after the other finishes, invalid
  inputs, the literal default; bundle and source modes; full suite 348 OK.
  Reinstalled all four workflows (runtime.py is shared; `source_review` by its upgrade procedure, rollback copy kept
  outside the repo); the match check shows every workflow and profile equal to the branch.
  Live, versioned fixture (`create_fixture.py`), scratch `repository_root`, real CAO and Claude Code:

  | Run | `review_max_parallel` | Total | correctness | security | Findings |
  |---|---|---|---|---|---|
  | `source-review-parallel-20261013-p1` | 1 | 219 s | 22:36:02-22:36:46 | 22:36:46-22:37:30 | 2 (HIGH `AUTHN_AUTHZ` human, MEDIUM `CORRECTNESS` auto-fix) |
  | `source-review-parallel-20261013-p4` | 4 | 169 s | 22:39:43-22:40:27 | 22:39:43-22:40:26 | the same 2, same routes |

  The reviewer stage went from 88 s to 44 s; the rest is unchanged. Both runs `REVIEWED`, coverage `INCOMPLETE`
  (fixture context), every step on its first attempt, no CAO sessions left open.
- **Step 3 done (2026-10-13).** New profile `sdlc_source_standards` (installer role list now six), validator profile
  told how to judge a cited rule. Contracts: `MAINTAINABILITY` (only with `standard_ref`), `standards_contract`
  (every rule of the batch exactly one verdict, `VIOLATED` lists exactly the findings citing the rule, the cited rule
  must cover the finding's file, head side), the validator may not change a `standard_ref`, `route_finding` takes the
  rule levels. `main` loads the rules from `repository_root` before any work; `prepare_standards` selects them against
  the head files and stages them in `workspace/knowledge/`; the batches run in parallel with correctness and security,
  each prompt ending with its batch index. Report: `standards` (files and digests, settings, applicable, skipped,
  batches, verdicts), `suggestions`, a `SUGGESTION` queue; comments and draft show the rule; the run output counts
  suggestions. Without `review_knowledge` prompts, steps and report are as before (tested).
  Design details settled while building:
  - D9 is enforced by Python: a rule finding's severity is capped by its level (`MANDATORY` MEDIUM, `NICE-TO-HAVE`
    LOW) without the "unless a reachable failure is shown" exception; a reachable failure is the correctness and
    security reviewers' job and keeps its own severity there.
  - D11: a validated `NICE-TO-HAVE` finding gets route `SUGGESTION` (in `findings`, never selected by remediation).
    D14: a rule finding outside the added lines goes to `report["suggestions"]` (not validated, `LOW`,
    `outside_changed_lines`), so Source remediation's exact re-check of `findings` is unchanged; the draft lists
    suggestions in the general comment.
  - A rule finding's stable id also includes `standard_ref` and its title: one rule broken several times in one symbol
    must not collapse into one id (which fails the run as an unresolved duplicate). Ids of other findings unchanged.
  - Source remediation recomputes routes with the levels from the staged `workspace/knowledge/index.json` and accepts
    the `SUGGESTION` queue (older reviews without it still load).
  Tests: 358 OK (bundle) and the Source review/remediation suites in source mode.
  Reinstalled `source_review` (upgrade procedure, rollback copy outside the repo) and `source_remediate`; every
  workflow and profile matches the branch.
  Live, real CAO and Claude Code, `review_max_parallel` 4, fixture with a correct `calc_fee(amt, cur)` using `r` and
  `0.01`, a `# Rule removed on 20-Jan` comment, an untouched abbreviated `fmt_amt(a)` in the base, and a PR edit that
  turns its own copy of NAMING-1 into NICE-TO-HAVE:

  | Run | Rules | Batches | Total | Rule findings | Verdicts |
  |---|---|---|---|---|---|
  | `source-review-rules-20261013-sample` | 2 | 1 | 209 s | COMMENTS-1 (line 16), NAMING-1 (17 `calc_fee, amt, cur`; 19 `r`) | both VIOLATED |
  | `source-review-rules-20261013-fifty` | 50 (8 apply, 42 skipped) | 3 (3, 3, 2) | 272 s | the same three, PY-3 (`0.01`), PY-2 type hints as `SUGGESTION` | PY-1, PY-4, PY-5, PY-6 NOT_VIOLATED |

  `fmt_amt` was not reported. The staged rules equal the trusted root's file (sha256 `65c35baa…`), and NAMING-1 stayed
  MANDATORY, so the PR's edit had no effect. Batches overlapped with correctness and security (batch 3 started when
  the first call finished: cap 4); batch 3 needed one contract repair (a line range outside the file). Every
  `MANDATORY` finding went to `HUMAN_REQUIRED` because the mapper marked `app/fees.py` as `BUSINESS_RULES` and
  `PUBLIC_API`: existing routing, a protected boundary forces a human for every finding in that file. No CAO sessions
  left open.
- **Step 4 done (2026-10-14).** New module `delivery_standards.py` and read-only profile `sdlc_standards_reviewer`
  (`profiles/standards-reviewer.md`, installed with `cao install`). Each review round of Delivery (both modes) selects
  the rules whose applicable files the round's diff adds lines to, stages them under
  `runtime/<ticket>/<run>/knowledge/r<n>/`, and runs the batches beside the PR reviewer through `run_parallel_steps`
  (new input `review_max_parallel`, default 4). The standards answer is validated (one verdict per rule, `VIOLATED`
  lists exactly the citing findings, the rule covers the file, the line range exists at the PR HEAD) and classified by
  `classify_pr_review` inside the contract step, so a bad category or impact gets the repair turn. Finding ids are
  `STD-r<n>-b<k>:<id>`; impact is capped by level before routing (D9). `NICE-TO-HAVE` findings and findings outside
  the added lines go to `suggestions` (`automation_eligibility: SUGGESTION`, LOW, never remediated, never blocking:
  D11, D14). From round 2 a rule is reviewed again only if one of its covered files changed since the previous round's
  reviewed commit, or it had no verdict before; the others keep their verdict, findings and suggestions with
  `carried_from_round` (D15). Each `pr-review-r<n>.json` carries `standards` and `suggestions`; the manifest records
  files and digests, settings and every round; the brief gets a "Project rules" section (verdict counts, not-assessed
  rules with the reviewer's notes, suggestions). Without `review_knowledge` prompts, steps, records and the brief are
  unchanged. `added_lines` moved to `review_knowledge.py` (shared with Source review). The integration fixture strips
  `review_knowledge` from the copied project file; a new integration test covers rules end to end, including
  carry-forward. Tests: 364 OK, Delivery suites also in source mode.
  Reinstalled `sdlc_deliver` and, by its upgrade procedure, `source_review` (its bundle changed with the move); every
  workflow and profile matches the branch.
  Live: throwaway clone in the session scratchpad with `main` = this branch plus a commit declaring the sample file and
  six Python rules (PY-1 docstrings, PY-2 type hints NICE-TO-HAVE, PY-3 magic numbers, PY-4 bare except, PY-5
  f-strings NICE-TO-HAVE, PY-6 print). Run `deliver-rules-20261014-1`, hybrid mode, the approved PAY-DEMO-001 plan, the
  implementer not steered: `AWAITING_HUMAN_REVIEW` in 724 s, 2 review rounds, 1 remediation.
  - Round 1 (one batch of 8 rules, beside the PR reviewer: both started after verification at 21:50:53Z and finished
    at 21:53:00Z): 12 rule findings, all `AUTO_FIX` (PY-1 missing docstrings, 1 capped from MEDIUM; NAMING-1 `exc`,
    `conn`, `_tmpdir`), 2 PY-2 suggestions; the PR reviewer's 5 findings unchanged in kind.
  - The remediator fixed all 12 and PR-004 in one commit (e.g. `exc` -> `integrity_error`, docstrings added).
  - Round 2 (both reviewers again from 21:54:34Z, finished 21:55:41Z and 21:56:08Z): all 8 rules reviewed again
    (every rule covers `app/**/*.py` and the remediation changed five Python files, so nothing could carry), 7 not
    violated, PY-2 two suggestions; no rule finding left. The brief lists them under "Project rules". No CAO sessions
    left open. Carry-forward is covered by the integration test, not by this run.
- **Revision after the user's answers (2026-10-14): no severity cap, business-sensitive code to a human.** D9, D11
  and D14 revised (section 3): the step 3/4 cap is removed in both workflows (`RULE_SEVERITY_CAP`/`IMPACT_CAP` gone;
  suggestions keep the reviewer's severity); prompts and both standards profiles give the normal level and allow a
  higher one when the violation also causes a concrete problem; a `NICE-TO-HAVE` finding rated HIGH is routed
  normally (to a human). D6: Source review already sends every finding in a sensitive-boundary file to a human;
  Delivery gains `route_sensitive_files`, so a rule finding in a file the PR reviewer flags with a protected category
  in the same round becomes `DEVELOPER_REQUIRED` with the reason. Tests updated and added (the HIGH exception in both
  workflows, the sensitive-file rule); 365 OK. Reinstalled `source_review` (upgrade procedure), `sdlc_deliver`,
  `source_remediate` and `sdlc_standards_reviewer`; everything matches the branch.
  Live: `source-review-nocap-20261014` (rebuilt fee fixture, sample rules, 197 s): the same three rule findings, the
  reviewer chose LOW for all, all `HUMAN_REQUIRED` because the mapper marked `app/fees.py` `BUSINESS_RULES` and
  `PUBLIC_API`. `deliver-rules-20261014-2` (fresh clone, 866 s, `AWAITING_HUMAN_REVIEW`): 3 review rounds, 2
  remediations; round 1 14 rule findings (PY-1, NAMING-1, LOW or MEDIUM), round 2 six more PY-1 docstrings on test
  doubles that round 1 had not reported, round 3 only PY-2 suggestions. No finding was rated HIGH. No PR finding had a
  protected category, so the sensitive-file rule did not trigger live (unit-tested). Observation: one batch of 8 rules
  did not report every PY-1 occurrence in round 1, which cost an extra remediation round.
- **Step 5 done (2026-10-14).** `review_knowledge.py`: `rule_may_apply` (a file path must match a rule's glob; a
  folder matches generously when it and the glob's literal start lie on one path), `stage_author_rules` (every rule
  once per run under `runtime/<ticket>/<run>/knowledge/authors/rules/`) and `author_rules_guidance` (a short index of
  the rules that can apply, plus every cited rule; empty without rules). Delivery: the single-mode implementer and
  its repair turn get the rules for the source roots; `run_hybrid(rules_for=...)` gives the supervisor (dispatch) and
  the integration the rules for the roots and each worker the rules for its `owns`; the remediator gets the rules for
  the files of its findings plus every cited rule. Source remediation: the remediator and the fix reviewer are pointed
  at the rules the review staged (`<review working dir>/workspace/knowledge/`), naming the cited ones; nothing is added
  for reviews without rules. Tests: helpers, hybrid prompts, Delivery prompts in the rules integration test, Source
  remediation with and without staged rules; 371 OK, source mode too. Reinstalled `sdlc_deliver`,
  `source_remediate` and `source_review` (upgrade procedure); everything matches the branch.
  Live: `deliver-rules-20261014-3`, fresh clone, same eight rules, hybrid, 951 s, `AWAITING_HUMAN_REVIEW`, 3 rounds, 2
  remediations. Round 1 rule findings fell from 12 and 14 (runs 1 and 2, authors without rules) to 7: no `exc`,
  `conn` or `_tmpdir` any more; what remained were six PY-1 docstrings on test helpers' `__init__`/`setUp` and one
  NAMING-1 (`db_path`). Round 3 only PY-2 suggestions. The rules index reached the agents' prompts (17 terminal logs
  contain it). The supervisor's first dispatch needed one contract repair (task T7 with an empty `owns`); the earlier
  runs did not, and the rules text cannot be excluded as a cause.
- **Step 5b done (2026-11-16): every file checked (D16, D17).** `review_knowledge.py`: setting `batch_max_files`
  (default 5, 1-50); `build_batches` groups rules as before and splits each group's files into even chunks, a rule's
  `covers` narrowed to the chunk; `check_file_verdicts` (one verdict per rule and covered file, `VIOLATED` lists exactly
  the citing findings in that file; `fill_missing` turns a missing one into `NOT_ASSESSED` plus a gap), `rule_status`,
  `not_assessed_pairs`, `batch_summary`, `diff_for_files`. `runtime._run_json_contract_step` gains `final_validator`,
  used on the last attempt only. Delivery: the prompt holds only the batch's part of the diff; records keep
  `verdicts` (per rule), `file_verdicts`, `not_assessed` as "RULE in file", batches as `{"rules", "files"}`;
  carry-forward keeps per-file verdicts. Source review: `knowledge/diff-b<k>.patch` per batch, the same report keys,
  a gap per not-assessed rule and file. Both prompts and both standards profiles ask for every occurrence and a verdict
  per file. Tests: 381 OK, source mode too; 4 mutations (missing file accepted, no cap, unfiltered diff, unchecked
  finding ids) all caught. The integration test covers a repair turn that still omits a file: the run continues and
  the brief lists "R-1 in app/x.py". Reinstalled all four workflows (shared `runtime.py`) and both standards profiles
  (`source_review` by its upgrade procedure); everything matches the working tree.
  Live 1, Source review `source-review-filecap-20261116-1` (scratch root with the sample rules plus PY-1, PY-3, PY-4;
  a local repo whose head adds 7 billing modules with 21 planted violations: PY-1 x14, NAMING-1 x4, PY-4 x2, PY-3 x1):
  2 batches (4 + 3 files, 5 rules each), both first attempt, per-file verdicts in the new shape, about 90 s in
  parallel. **All 21 planted violations reported, no rule false positive**, plus one real DATA_LOSS finding
  (`save_invoice` truncates before writing). All routed `HUMAN_REQUIRED` (billing marked business-sensitive by the
  mapper: existing behaviour). No baseline run without the cap on the same fixture, so the gain is not measured
  against the old code.
  Live 2, Delivery `deliver-filecap-20261116-1` (fresh clone, the sample rules plus PY-1..PY-6 as on 2026-10-14,
  hybrid, approved PAY-DEMO-001 plan, implementer not steered): `AWAITING_HUMAN_REVIEW`, 3 rounds, 2 remediations,
  every step first attempt. Each round 2 batches (3 source files, 3 test files; 8 rules each). Round 1: 6 NAMING-1
  findings, every other rule not violated in every file; round 2 only the one NAMING-1 finding round 1 had routed to
  the developer (now `AUTO_FIX`); round 3 clean. No rule finding appeared late (2026-10-14: six PY-1 found only in
  round 2). An AST scan of the round 1 and final code confirmed PY-1: all 40 (46) public definitions the change adds
  have docstrings, so the authors followed the rules and this run could not measure recall; live 1 does. No CAO
  sessions left open; the throwaway folder was deleted.
- **Step 6 done (2026-11-16): docs.** New `reference/project-rules.md` (rule file format, settings with bounds, how the
  reviewers batch and give per-file verdicts, findings and suggestions, authors, trust and audit, cost). Updated:
  `workflows/delivery.md` (agents, review step, input `review_max_parallel`, records, `failed` cause, configuration
  table, project-file key, new section "Project rules", forbidden roots), `workflows/source-review.md` (parallel
  reviewers, standards stage, rule findings in the routing policy, six profiles and the upgrade `rm` list, input,
  `knowledge/` folder, report keys, `SUGGESTION`, invalid rules, new section "Project rules"),
  `workflows/hybrid-delivery.md`, `workflows/source-remediation.md`, `reference/agent-profiles.md` (two profiles, 16
  files), `reference/write-scope-hook.md` (protected folders, read-only profile), `architecture.md`,
  `build-and-install.md`, both policies and both workflow contracts (the PR review policy and the Delivery contract are
  read by the agents), the docs index and the root README. All links and anchors checked by script; all 19 Mermaid
  diagrams in the changed pages parse; the JSON example validated with the real loaders; 381 tests OK.

