# Future versions: remaining workflow gaps

This guide records findings outside the three fixes in [hardening-plan.md](hardening-plan.md), with mitigation direction and checks for reassessment. It is not an implementation schedule or a claim that mitigations exist.

Assessment reference: **2026-09-21, HEAD `abcea97` plus uncommitted changes**. The working tree and installed CAO bundles can differ. Priorities use MoSCoW relative to operational use beyond the learning prototype.

## Reassess before acting

- Record the current commit, relevant working-tree changes, installed bundle/profile versions, and execution environment. Follow the behavior if functions or files have moved; old line numbers are not evidence.
- Inspect current implementation and configuration, then read the newest verification records. Distinguish mocked tests, real enforcement checks, and agents merely following instructions.
- Reproduce the relevant condition in a disposable repository/environment. Do not publish reviews, modify production systems, or test isolation against real credentials.
- Classify each finding as **open**, **partially mitigated**, **resolved**, or **not applicable**, with dated evidence and its limits. A new API, configuration flag, or passing happy-path run alone does not establish closure.

## Must: isolate verification execution

**Finding:** Delivery executes agent-editable tests and application imports through host subprocesses. The Claude write hook does not intercept Python filesystem operations or child processes. Fixed command arguments and a timeout do not isolate executed code. Host-level restrictions may reduce exposure; they were not established by this review.

**Mitigation:** Run verification against an exported candidate-commit snapshot in a disposable, restricted container or equivalent sandbox. Provide only required files and pinned toolchains; isolate writable scratch space; exclude credentials, governance records, the original checkout, and control sockets. Restrict network access and privileges, bound resources/logs, and terminate the entire workload on timeout. Keep result recording outside the sandbox and bind evidence to commit and execution-image/configuration digests. Fail closed if isolation is unavailable. Prefer a rootless runtime where supported; validate actual resource-limit enforcement.

**Reassess:** Trace `_run_verification()` in [delivery.py](.agentic-sdlc/cao/sdlc_workflows/delivery.py), including repair and remediation. Inspect the actual runtime mounts, environment, identity, networking, and cleanup. Use harmless sentinel files and dummy secrets to demonstrate that host writes, secret reads, outbound connections, and surviving child processes are prevented. A container invocation alone is insufficient evidence.

## Should: isolate concurrent delivery runs

**Finding:** Delivery switches branches in a shared checkout and writes ticket-level records. Sequential workers within one run do not prevent another run or a developer from changing the same checkout. Clean-tree checks cannot enforce exclusivity throughout execution.

**Mitigation:** Enforce an exclusive checkout lock for the simpler operating model, or allocate a separate worktree/clone and branch per run. Keep evidence run-specific and serialize promotion of ticket-level results with expected-version checks and atomic publication. Define ownership, stale-lock recovery, and interrupted-run handling. Until enforced, require exclusive use of the delivery checkout.

**Reassess:** Inspect branch selection and manifest writes in [delivery.py](.agentic-sdlc/cao/sdlc_workflows/delivery.py) and persistence in [artifacts.py](.agentic-sdlc/cao/sdlc_workflows/artifacts.py). In disposable repositories, start overlapping same-ticket and different-ticket runs and interrupt one. Closure requires deterministic rejection or isolation, no mixed commits/evidence, and safe recovery. Worktrees alone do not protect shared ticket records.

## Should: complete the remote PR and human-review handoff

**Finding:** Delivery prepares local PR artifacts and records `PR_CREATED` with no remote PR reference. The approval recorder checks a local branch and accepts a supplied reviewer identity/reference; it does not verify a source-control review. This is a documented prototype boundary, not evidence of remote approval.

**Mitigation:** Add explicit, authorized PR creation/update and a remote review adapter. Bind approval to repository, PR identity, current remote HEAD, and an eligible human review under repository policy. Invalidate stale approval after code changes, handle dismissed/rejected reviews, and route human comments through the existing remediation policy. Keep local preparation distinguishable from remote approval and make external operations idempotent.

**Reassess:** Inspect the PR stage, [record_pr_approval.py](.agentic-sdlc/scripts/record_pr_approval.py), and the delivery contract. Use API fixtures for moving HEADs, stale/dismissed reviews, unauthorized reviewers, duplicate events, and missing PRs. Validate the actual integration in an explicitly authorized test repository before claiming end-to-end remote approval.

## Could: expand representative live validation

**Finding:** Successful Python fixtures do not establish reliability for every registered skill, toolchain, or recovery path. The [2026-09-20 hybrid check](agentic-sdlc-docs/verification/hybrid-delivery-live.md) omitted skills, repair, and remediation. The newer [source-root check](agentic-sdlc-docs/verification/configurable-source-roots-live.md) exercised automatic remediation and a failing verification-repair path, so the earlier blanket gap is already partly outdated. Skill-selected toolchains and a real multi-module specialist scenario remain unexercised in those records.

**Mitigation:** Maintain a small coverage matrix for single/hybrid mode, selected skills, restricted specialist profiles, verification failure, successful repair, remediation, timeout, and missing tooling. Add representative live fixtures with pinned dependencies and expected outcomes; retain commit/configuration versions and evidence. Verify that skill-required suites run again after changes.

**Reassess:** Compare the current registry and runner paths with the latest dated records and tests. Count a path as covered only when it actually executed and its result was checked. Narrow the finding as evidence improves; do not keep reporting already-validated paths as missing.

## Should: align planning scope and failure reporting with source roots

**Additional observation from the newer baseline:** Planning does not yet consume configured write roots. A plan can therefore require changes Delivery cannot make. The source-root live check also recorded a no-change repair exception leaving the manifest at `IMPLEMENTED` while the run ended `failed`.

**Mitigation:** Provide effective source roots to planning/review and flag tasks outside them before human approval; revalidate at delivery. Convert expected scope/repair failures into consistent `BLOCKED` outcomes with a reason, preserved evidence, and an operator action. Coordinate shared error handling with the hardening work.

**Reassess:** Inspect planning prompts/contracts and delivery exception paths rather than relying on the old fixture result. Run a plan requiring an out-of-root change and a repair that produces no changes. Confirm early scope detection and agreement between workflow outcome, manifest state, and human-facing guidance.

## Won't for now: parallel workers and deployment automation

These are deferred capabilities, not defects in the current learning scope. Parallel workers require isolated workspaces, task ownership, deterministic integration, and concurrency controls first. Deployment requires a separate environment-specific authority boundary, artifact-bound human approval where required, constrained credentials, idempotent operations, and recovery/rollback evidence.

**Reassess:** Revisit when a concrete throughput or release requirement justifies the capability and its prerequisite controls have evidence. Registry or skill changes alone do not authorize parallel shared-checkout writes or external-system mutations.
