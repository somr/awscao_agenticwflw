# Workflow hardening implementation plan

## 1. Rationale

The project already separates planning, human approval, implementation, verification, and independent review. Three enforcement gaps can nevertheless undermine that separation:

1. **Agents can modify evidence belonging to other agents.** The repository hook permits writes anywhere under `.agentic-sdlc/runtime/`, while the generated source-review hook permits the equivalent subtree in its isolated workspace. Instructions request one answer file, but enforcement grants much more. A mistaken or injected worker could overwrite earlier evidence or prepopulate a later answer.
2. **An approved plan can be applied to materially changed code.** Delivery checks whether the recorded baseline is an ancestor of the base branch. Ancestry remains true after changes to APIs, tests, dependencies, or business behavior that invalidate the plan. The actual existing delivery branch also needs validation.
3. **Single-mode delivery can commit unrelated local work.** The clean-source-tree and empty-index preconditions are enforced for hybrid delivery, but not consistently for single mode. Staging the permitted source directories can collect pre-existing edits, and an ordinary Git commit includes unrelated files already staged in the index.

This plan turns these assumptions into deterministic checks. The intended result is that each agent can write only its assigned output and permitted application source, each implementation starts from a baseline compatible with its approval, and delivery does not absorb a developer's unrelated work.

Verification sandboxing is deliberately deferred. These changes protect the workflow's agent tool boundaries and Git/approval handling; they do not contain arbitrary application code executed by verification on the host.

## 2. Scope and current baseline

Status: **planned; no hardening implementation is included in this document**.

Prepared on 2026-09-21 against HEAD `abcea97` and the current uncommitted working tree. The configurable-source-root work has advanced since the initial review: its plan now records implementation and live verification, and Delivery uses `source_config.py` and registry-defined verification commands. Those results are existing documentation claims, not checks rerun while writing this plan.

Implementation must build on that work. Use configured source roots and per-profile restrictions rather than reintroducing hardcoded `app/` paths. Reinspect the working tree before each milestone because development is occurring concurrently; preserve unrelated edits.

### In scope

- Common preconditions and commit-scope checks for single, hybrid, repair, and remediation paths.
- Exact answer-file authorization across planning, delivery, and source review.
- Deterministic baseline comparison, machine-readable blocking evidence, and a supported plan reapproval path.
- Regression tests, source/bundle parity, focused live validation, deployment instructions, and necessary contract/profile updates.

### Out of scope

- Verification containers or other execution sandboxes.
- GitHub PR creation, remote review verification, deployment, and downstream QA.
- Parallel workers, per-run delivery worktrees, and comprehensive repository locking.
- Semantic proof that a plan covers all requirements or that a test suite proves correctness.
- Windows support and a general-purpose rewrite of all artifact persistence.

Delivery still requires exclusive use of its checkout while it runs. Preflight and commit checks detect specific conflicts; they cannot distinguish a developer's simultaneous in-scope edits from an agent's edits. A later locking/worktree change is needed to enforce full checkout isolation.

## 3. Delivery order and effort

Estimates are engineering days for one developer, including relevant regression tests, documentation, and a focused live check. They exclude operator waiting time and unrelated source-root follow-ups.

| Milestone | Deliverable | Estimate | Exit condition |
|---|---|---:|---|
| M0 | Reconfirm baseline and inspect CAO authorization lifecycle | 0.5 day, included below | Exact answer binding has a viable implementation path |
| M1 | Shared clean-tree/index and commit-scope enforcement | 0.5–1 day | Both modes preserve unrelated work in negative tests |
| M2 | Exact answer-file authorization | 2–3 days, including M0 | All three workflows deny cross-step and cross-run writes |
| M3 | Drift gate and approval revision handling | 2–4 days | Relevant drift blocks; reviewed reconciliation can proceed |
| M4 | Integrated deployment and acceptance checks | Included in M1–M3 | Repository and installed artifacts agree; live checks pass |

**Expected total: 4.5–8 engineering days.** Allow another 1–3 days if CAO cannot establish trusted step identity before worker execution and requires an adapter or upstream lifecycle change. Re-estimate M2 after M0; do not weaken identity checks to preserve the estimate. Approval revision compatibility is the main uncertainty in M3.

## 4. M0 — confirm prerequisites

1. Record HEAD, working-tree changes, configured roots, registry suites, and installed workflow/profile versions without modifying unrelated files.
2. Inspect the installed `cao_workflow.step` implementation and available terminal metadata. Determine precisely when a terminal is created, when it starts processing the prompt, and when `step()` returns a handle.
3. Establish whether immutable server-side metadata identifies repository/workspace, workflow run, step, and execution attempt. A profile name alone is insufficient: many workers share the implementer profile.
4. Prove one race-free authorization path in a disposable fixture before refactoring the hook:
   - Preferred: write a protected step authorization before dispatch and have the hook match it against server-confirmed run/step/attempt metadata.
   - Otherwise: use a supported create/register/start sequence that binds the terminal ID before it can execute tools.
   - If neither is available, introduce a trusted lifecycle adapter or CAO change. Treat this as an explicit dependency, not an assumed feature.
5. Confirm that source-review's generated workspace guard can use the same binding mechanism while preserving its isolated-source behavior.

Do not register permissions only after a blocking `step()` returns: the worker may already have needed to write its answer. Do not derive authorization from prompt text, a model-provided filename, profile identity alone, or worker-supplied claims about its step.

## 5. M1 — protect local work and commit boundaries

### 5.1 Implementation

Add shared delivery helpers with descriptive names, for example `check_delivery_worktree()` and `commit_source_changes()`. Apply them to both modes and every repair/remediation commit path.

Before branch switching or agent dispatch:

- Validate configured roots and resolve their filesystem boundaries using the existing source configuration helpers.
- Require an empty Git index across the whole repository.
- Require no tracked or untracked changes under any source root; handle tracked deletions and unmerged entries explicitly. Ignored build outputs should retain their existing Git behavior.
- Resolve and record the intended starting branch/commit. Reject an unresolved merge or other conflicting Git operation with a useful reason.
- Do not automatically stash, reset, clean, commit, or discard developer changes.

After selecting the delivery branch, repeat the applicable checks and confirm the expected HEAD. Re-read configuration if checkout changed its tracked files; reject an unexpected change rather than mixing policies from two branches.

Before each agent implementation/repair/remediation batch, establish a clean source tree and empty index. After the agent finishes:

1. Confirm the expected branch and HEAD have not changed externally.
2. Require the index still to be empty before staging; agents do not own Git operations.
3. Stage only approved configured roots using the existing source-root helper, extending it where necessary to handle deletion of the last tracked file in a root.
4. Inspect the staged diff using NUL-delimited paths and literal path handling. Validate additions, modifications, deletions, and both sides of renames; disabling rename detection for this check is acceptable.
5. Refuse a commit if any staged path falls outside the configured roots. Never rely on the agent's `files_changed` declaration as the source of truth.
6. Commit only after these checks succeed; record the resulting commit and actual changed paths.

Keep unstaged changes outside source roots where Git permits branch switching. If a guard fails after staging, preserve the index and worktree for inspection, explain what was staged by the workflow, and do not perform an automatic rollback.

### 5.2 State and operator experience

Return a clear blocking reason such as `dirty_source_tree`, `nonempty_git_index`, `unexpected_repository_head`, or `staged_path_outside_source_roots` with affected paths. Use a run-local failure artifact when preflight fails before a new delivery manifest exists; do not overwrite an earlier successful delivery record merely to report a rejected invocation.

For failures during an active delivery, persist `BLOCKED` consistently in both modes. Limit this work to failures introduced or handled by these guards; a general exception-state overhaul remains separate.

### 5.3 Tests and acceptance

Use real temporary Git repositories to exercise:

- Single and hybrid mode with pre-existing staged files outside the roots: no agent starts and nothing is committed.
- Tracked edits, untracked source files, deletions, and merge conflicts inside roots: reject before implementation.
- Unstaged unrelated documentation outside roots: preserve it and exclude it from the commit.
- Multiple roots, not-yet-created roots, and deletion of an entire tracked root.
- External staging or HEAD changes after dispatch: block the commit and preserve evidence.
- Repair and remediation: use the same commit guard.
- Normal clean runs: retain current commits, verification, and review behavior.

**Acceptance:** every workflow-owned application commit is limited to configured roots; both modes enforce the same initial local-work protections. No test relies solely on mocked Git output for these guarantees.

## 6. M2 — bind evidence writes to the producing step

### 6.1 Authorization contract

Introduce a small, versioned authorization record owned by Python orchestration. Proposed location: `.agentic-sdlc/control/`, explicitly denied to every worker and excluded from version control. Source-review workspaces get their own protected control directory. Use separate per-assignment files and atomic replacement rather than one shared mutable allowlist.

Each grant must bind:

- Schema version and canonical repository/workspace identity.
- Workflow run ID, step ID, and execution-attempt identity.
- Expected agent profile and the trusted terminal/step identity established in M0.
- One exact absolute answer-file path, constrained to the expected run's runtime tree.
- Active/revoked state and bounded validity consistent with the step timeout.

Grant creation must reject malformed identities, ambiguous duplicate assignments, path traversal, symlinked control directories, and answer paths that escape the run. Grants are not bearer tokens: discovering a filename or reading a record must not authorize another terminal.

The control directory is protected by the worker tool boundary, not by an assumed separate OS user. The deferred host-verification exposure remains a separate risk.

### 6.2 Hook decision procedure

For a CAO worker:

1. Parse the requested write operation; malformed or missing target information fails closed for the matched write tools.
2. Resolve and validate the target and trusted workspace root. Protected tooling, control files, and durable governance records remain denied.
3. Resolve the active assignment through trusted metadata. Missing, expired, revoked, mismatched, corrupt, or ambiguous authorization denies writes.
4. For an answer write, require equality with the exact authorized filename. A parent-directory prefix match is insufficient.
5. For an application write, additionally require a server-confirmed write-capable profile and membership in its configured source roots. Read-only profiles receive no application access.
6. Deny every other runtime path, including another step's answer, normalized context, raw logs, verification evidence, and earlier review output.

Preserve the repository hook's explicit non-CAO interactive-session behavior. Preserve source-review's stricter isolated-workspace behavior: its guard must not become unrestricted when identity is unavailable. Retain allowed read access; this milestone does not add read isolation between reviewers.

Reject symlink targets/parents where they could redirect exact-path authorization, and ensure Python creates answer directories. Document that protection against an unrelated same-user process racing filesystem changes requires stronger process isolation.

### 6.3 Runtime lifecycle

- Create required directories and establish authorization before the worker can write.
- Give each contract-repair attempt a distinct assignment and answer path.
- Refuse unexpected pre-existing answers for a fresh attempt. Preserve them as conflict evidence instead of silently consuming them.
- When output stabilizes, revoke write permission before publishing canonical evidence or starting a dependent step. Clean up only the corresponding terminal in `finally` paths.
- Preserve existing stable-answer and contract-validation behavior, including free-form Markdown outputs.
- For a CAO replay, accept a prior completed answer only when a trusted completion receipt binds the run, step, attempt, profile, answer path, and digest. Do not recreate an active grant merely because a file exists.
- On timeout, cancellation, malformed output, or orchestration failure, revoke permissions. If cleanup fails, leave a diagnostic; an orphan terminal must not retain an active grant.
- Bound orphan grants after a workflow crash. Check lease expiry and terminal/run liveness; define explicit recovery handling for an incomplete attempt rather than granting access from leftover files.

The generated guard in `source_review.py` must enforce the same contract. Prefer one shared guard implementation packaged/copied by trusted orchestration; if duplication is necessary, test both implementations against the same authorization corpus.

### 6.4 Tests and acceptance

- Assigned answer succeeds; sibling file, another step, another run, and protected evidence all fail.
- Two terminals using the same profile cannot write one another's answers.
- Correctness and security source-review agents cannot overwrite each other's evidence.
- Implementers retain only configured application writes; reviewers and supervisors cannot modify source.
- Missing server identity, API failures, malformed grants, revoked/expired grants, and forged prompt paths fail closed.
- Absolute/relative targets, traversal, symlinks, notebook writes, and malformed payloads are covered.
- Worker writes immediately at startup: no authorization race.
- Contract repair gets a fresh authorized output; replay verifies receipts; digest mismatch blocks replay.
- A terminal surviving cleanup cannot write after revocation.
- Planning, delivery, and source-review fixture runs complete using the installed bundles and real hook.

**Acceptance:** a worker can write only its assigned answer and, where applicable, its configured source paths. Python owns canonical artifacts. No blanket runtime-write exception remains in either guard.

## 7. M3 — detect drift and support reviewed reconciliation

### 7.1 Approval identity

Strengthen `_check_plan_approved()` to cross-check ticket identity, plan digest, baseline SHA, and guidance digest where present between the active plan, execution manifest, and approval record. Resolve the baseline as an actual commit and retain the ancestry requirement.

Resolve the base branch to an immutable commit before making decisions. Determine the actual proposed starting commit: an existing delivery branch if present, otherwise the pinned base tip. Inspect both the base tip and actual starting commit against the approved baseline; require compatible history and validate again after checkout.

Do not automatically accept an existing delivery branch simply because its name matches the ticket. A branch containing earlier implementation changes should block in v1 unless an explicitly validated resume path proves the plan, run, commit, and completed-step relationship. The conservative default is a diagnostic asking the operator to reconcile the branch; never reset or delete it automatically.

### 7.2 Conservative comparison policy

Use a deterministic tree comparison with external diff/text-conversion execution disabled and NUL-delimited changed paths. Include additions, removals, type changes, and rename endpoints. Record all changed paths before classifying them.

For the first version, **unknown changes block**. This avoids claiming that a short list of recognized dependency filenames captures every language and build system.

Always treat these areas as relevant:

- Configured source roots and their tests.
- Dependency manifests/locks, build configuration, verification entry points, and CI configuration wherever they live.
- The specialist registry, source-root/write-profile settings, agent profiles, skills, workflow implementation, hooks, and governance policies.
- Submodule pointer changes and unresolved source dependencies; block with an explanation if the comparison cannot establish their contents.

Allow a small maintainer-owned list of non-executable project documentation paths to advance without new plan approval. Ensure source roots and protected tooling override documentation exclusions. Documentation that shapes requirements or is referenced by the plan remains relevant. Do not let agents define exclusions.

Treat newly committed copies of the exact approved plan/approval artifacts as bookkeeping only after validating their contents against the active approved record; otherwise an operator committing approval records would invalidate its own baseline. Do not exempt the whole records directory indiscriminately.

Relevant net tree changes block even if a model judges them harmless. Report the accepted starting SHA separately from the approved baseline; do not rewrite approval history to conceal accepted documentation-only drift.

### 7.3 Gate result and failure behavior

Persist a run-local drift report with:

- Policy/schema version, ticket, run, plan digest, approval revision, and approved baseline.
- Pinned base tip, proposed delivery starting commit, and actual post-checkout HEAD when available.
- Changed paths, classification/reason, relevant configuration digests, and verdict.
- A concrete next action: proceed, reconcile plan, reconcile existing branch, or repair invalid approval evidence.

On relevant drift, return `BLOCKED` before implementation and avoid a new implementation commit. Preserve existing successful delivery evidence. Check relevant working-tree inputs as well as committed trees: dirty hooks, registries, dependency files, or build configuration must not silently override the checked commit. Allow only explicitly validated approval bookkeeping and unrelated non-executable documentation. If a checked ref or protected configuration changes during preflight, abort and require a fresh run; do not continue with a mixed snapshot.

### 7.4 Reapproval and immutable history

Simply telling the operator to rerun planning is insufficient today:

- `planning.py` refuses to overwrite an existing approved plan.
- `approve_plan.py` treats plan content hash alone as the immutable decision identity, so unchanged plan text cannot receive a new decision for a new baseline.
- Warm-start candidates reject a different baseline, correctly preventing stale analysis reuse.

Implement an explicit revision path rather than weakening these guards:

1. Add an operator-invoked planning revision input that names the expected currently approved revision/digest. Validate it before doing work. Normal planning continues to refuse silent replacement.
2. Publish the new planning candidate into a separate revision directory under the ticket records. Keep the existing approved revision and all its decision evidence unchanged.
3. Run fresh repository analysis and independent plan review against the new baseline. An old plan may be supplied as reference, but changed-baseline reconciliation must not reuse stale warm-start analysis as verified context.
4. Give each reviewable revision an identity binding at least ticket, plan digest, repository baseline, guidance digest, and review lineage. Same plan text at a different baseline is a different approval subject.
5. Extend the human approval recorder to select that revision explicitly, verify its reviewed state and all bound digests, and record a new immutable decision. Rejection leaves the prior active revision unchanged.
6. Promote a newly approved revision through a single atomically replaced active-revision pointer. Delivery resolves that pointer once and uses only the selected revision's artifacts.
7. Support legacy root-level records as the initial revision when no pointer exists. Preserve them as historical evidence on first promotion. Update consumers to use a shared resolver; compatibility copies, if retained for humans, must not become an alternate approval source of truth.
8. Prevent stale promotion with a per-ticket approval/promotion lock and an expected-current-revision check. This narrowly protects approval updates; it is not comprehensive delivery checkout locking.

No agent can approve a revision or promote it to active. Archive/revision paths must be immutable through the agent hook. Bind each delivery manifest to its selected revision so later promotion cannot silently re-label a previous delivery.

Before approval, verify that planning actually inspected the recorded baseline: run fresh analysis in a checkout/snapshot matching that baseline and detect relevant dirty files or HEAD changes. A baseline string in a prompt is not proof of repository identity. Reuse existing snapshot mechanisms where appropriate; do not switch the developer's working branch during planning without an explicit workflow design.

### 7.5 Tests and acceptance

- Unchanged baseline succeeds; relevant descendant changes block even though ancestry succeeds.
- Documentation-only change succeeds with an explicit recorded classification.
- Unknown build/dependency files, root changes, hook changes, deletions, renames, and submodule changes block.
- Exact approved-record bookkeeping is accepted; altered approval/plan evidence is not.
- Approval/manifest baseline or guidance mismatch blocks before any agent.
- Existing stale/divergent delivery branch is rejected despite a compatible base branch.
- Base ref or checkout changes between checks block; real temporary repositories exercise these transitions.
- Fresh revision requires new review and human approval; old decisions remain accessible.
- Identical plan text at a new baseline can be approved as a new revision; a repeated decision for the same revision is refused.
- Concurrent promotion or interrupted pointer publication cannot expose a partially approved revision.
- Legacy record resolution works without silent modification; warm-start baseline mismatch remains rejected.
- Planning's inspected snapshot agrees with the revision's baseline.

**Acceptance:** Delivery never equates ancestry alone with approval compatibility. Operators can reconcile a blocked plan without deleting old approval records or manually editing hashes.

## 8. Implementation map

Names of new modules below are proposals; preserve the existing modular bundle architecture.

| Area | Files or proposed modules | Change |
|---|---|---|
| Delivery guards | `.agentic-sdlc/cao/sdlc_workflows/delivery.py`, `source_config.py` | Common preflight, scoped commits, pinned start commit, drift gate |
| Agent lifecycle | `.agentic-sdlc/cao/sdlc_workflows/runtime.py`, proposed `write_authorization.py` | Grant/receipt lifecycle and replay validation |
| Repository hook | `.claude/hooks/restrict-write-scope.py`, `.claude/settings.json` if needed | Exact answer grants; preserve configured source restrictions |
| Isolated review | `.agentic-sdlc/cao/sdlc_workflows/source_review.py` | Replace broad generated runtime-write guard |
| Approval/revisions | `planning.py`, proposed `plan_records.py`, `.agentic-sdlc/scripts/approve_plan.py` | Reviewed revisions, active resolver, immutable decisions |
| Comparison | Proposed `baseline_drift.py` | Deterministic change classification and reports |
| Artifact/config lifecycle | `artifacts.py`, `.gitignore`, approval templates as needed | Atomic control records, revision pointers, schema fields |
| Packaging | `build_workflow.py`, `install_workflow.py`, packaging tests | Include dependencies; validate self-contained bundles and compatibility |
| Documentation | Planning/delivery contracts, governance, profile catalog, workflow guides | Explain new boundaries and operator recovery |

Update only affected profiles. Verify approval scripts remain runnable independently of a CAO workflow process. New shared helpers must not accidentally require importing `cao_workflow` from the standalone approval CLI.

## 9. Validation and rollout

### Automated checks

Run focused suites after each milestone, then the complete workflow suite in both supported modes:

```bash
python3 -m unittest discover -s tests
SDLC_TEST_SOURCE=1 python3 -m unittest discover -s tests
```

Extend `test_deliver.py`, `test_restrict_write_scope.py`, `test_source_review.py`, `test_approve_plan.py`, `test_workflow_integration.py`, and `test_workflow_packaging.py`; add dedicated drift/authorization/revision tests where the contracts warrant them. Retain source-config validator parity coverage.

Test behavior through real Git operations and hook invocations where these form the boundary. Use controlled CAO metadata fixtures for negative cases, then confirm identity binding against real terminals. Ensure bundle flattening introduces no conflicting top-level bindings. Validation must check actual denial and absence of unauthorized effects, not merely inspect prompts.

### Focused live checks

Use disposable clones accepted by the CAO server, with no publication remote and fresh run IDs:

1. Clean single and hybrid delivery reach the expected human-review state.
2. Dirty single-mode delivery refuses before agent dispatch and preserves existing changes.
3. Real worker can write its answer and allowed source; cross-step, cross-run, and control-file writes are denied.
4. Planning and source review complete with the new exact-output guard, including one controlled contract repair.
5. A relevant post-approval commit causes `BLOCKED`; a reviewed replacement revision can subsequently deliver.
6. Cleanup/replay handling preserves evidence and revokes stale authorization.

Record versions, commit/config digests, run IDs, outcomes, and limitations. Do not infer enforcement solely from an agent voluntarily following its instructions.

### Deployment

- Coordinate upgrades when affected runs have finished. Installed workflows and profiles are machine-wide, and changing a live hook can break an older active workflow.
- Update hooks/control support, profiles, and bundles as one coordinated release; validate their protocol compatibility before launching workers.
- Rebuild all workflows using the changed shared runtime, not only Delivery. Compare installed artifacts with repository-generated bundles.
- Respect the source-review installer's refusal to overwrite existing definitions; follow its documented upgrade procedure.
- Keep legacy record readers until explicit revision migration is validated. Existing in-flight runs without authorization receipts should finish before rollout or restart deliberately; do not grandfather them into broad runtime access.
- If rollback is needed, stop affected new runs and restore a matched set of code, hooks, profiles, and bundles. Preserve evidence and revision history. If old code cannot interpret promoted revision records, keep delivery blocked until a compatible version is restored; never strip approval metadata to make rollback run.

## 10. Impact on the current way of working

| Situation | New behavior |
|---|---|
| Normal clean hybrid delivery | Same user-facing flow; additional deterministic checks |
| Single delivery with pending source edits or staged files | Refuses to start; developer commits/stashes deliberately |
| Unstaged unrelated files outside source roots | Preserved where branch switching is safe; excluded from workflow commits |
| Custom worker writes extra runtime files | Denied unless a future explicit output contract authorizes them |
| Ordinary planning/review answer delivery | Transparent after coordinated upgrade |
| CAO metadata unavailable or authorization invalid | Writes denied with actionable failure evidence |
| Code changes after plan approval | Delivery blocks pending fresh analysis, review, and human approval |
| Approved plan text unchanged after reconciliation | New baseline-bound revision can receive its own human decision |
| Existing delivery branch already contains implementation | Requires explicit reconciliation/resume evidence; no blind rerun |
| Developer edits the same checkout during delivery | Still unsupported; exclusive checkout use remains required |

## 11. Definition of done

- [ ] Both delivery modes and all commit paths protect pre-existing local work.
- [ ] Every worker answer write is bound to one trusted step/attempt; no blanket runtime permission remains.
- [ ] Cross-role, cross-run, path-escape, and stale-grant negative checks pass.
- [ ] Drift checks evaluate both the pinned base tip and actual delivery starting commit.
- [ ] Human approval binds the selected plan revision, baseline, and guidance consistently.
- [ ] Reconciliation preserves old decisions and requires new independent review and human approval.
- [ ] Source and bundled test modes pass; relevant real-CAO checks are recorded.
- [ ] Installed artifacts match the reviewed repository version and use compatible authorization schemas.
- [ ] Guides document changed preconditions, recovery, migration, and the remaining exclusive-checkout requirement.
- [ ] Verification sandboxing and other deferred gaps remain explicitly identified as unresolved.
