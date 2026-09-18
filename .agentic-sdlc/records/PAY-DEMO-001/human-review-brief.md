# Human Review Brief — PAY-DEMO-001 / PR (local, not yet created)

## Review target

- Jira ticket: `PAY-DEMO-001`
- Pull request: `(local, not yet created)`
- Current PR HEAD SHA: `3260103e04fc4b3ca4e5ed79eab0342eae20c215`
- Approved plan digest: `c3dc197c4443ed402579c39a8235a04f6a0a7d32acfa2fc4887f37b68caa1b3f`

## Implementation summary

Implements the approved Development Plan (tasks: T1, T2, T3, T4, T5). Files changed: app/payment_service/repository.py, app/payment_service/payment_service.py, app/tests/test_payment_service.py. 2 autonomous remediation round(s) applied additional fixes for findings the workflow classified as auto-eligible.

## Automated review summary

- Findings detected: 6
- Automatically remediated: 3
- Developer-remediated: 0
- Remaining/escalated: 3
- Autonomous remediation rounds: 2

## Human attention required

### 1. PR-001: process_callback(), except Exception block wrapping self._fulfilment_service.fulfil(event.payment_id), lines ~32-40

- Location: `app/payment_service/payment_service.py`
- Impact: `MEDIUM`
- Why human attention is required: This is the transactional core of the idempotency mechanism -- concurrency/transaction semantics is a policy-protected area that is always DEVELOPER_REQUIRED regardless of confidence -- and the correct behavior when release itself fails (log-and-swallow vs. escalate vs. bounded retry, and how to preserve/chain the original exception) is an interpretive design decision the approved plan's D5 does not address (D5 only covers the happy-path release). This exact finding was raised against an earlier HEAD in this same delivery and remains unresolved in this diff, correctly so under policy (DEVELOPER_REQUIRED findings must not be auto-fixed), but per the convergence rule its persistence across rounds is itself grounds for developer escalation rather than another automated attempt.
- Requirement/plan reference: FR-2
- Original finding: fulfil(event.payment_id) raises a genuine failure (e.g. a RuntimeError from a downstream fulfilment error). Execution enters the except block and calls self._repository.release_for_retry(event.provider_event_id) unguarded; if that DELETE/commit itself raises (e.g. sqlite3.OperationalError from write contention beyond the 30s busy timeout, or any other persistence error), the new exception propagates instead of the original fulfilment failure -- the bare `raise` that is supposed to preserve D5's 're-raise the original exception unchanged' contract is never reached -- and, critically, the claim row for that provider_event_id is never actually deleted since the DELETE did not commit. — The caller/logs see an unrelated sqlite3 error instead of the real fulfilment failure, confusing incident diagnosis, and the processed_callbacks row stays claimed forever: no future retry with the same provider_event_id can ever re-claim and re-attempt fulfilment, silently producing the same permanently-stuck-payment state the plan already accepts as an out-of-scope limitation, but here via an unguarded code path in the newly-added retry mechanism rather than a documented limitation.
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `3260103e04fc4b3ca4e5ed79eab0342eae20c215`)
- Reviewer recommendation: Wrap the release_for_retry() call in its own try/except inside the failure branch so a secondary release failure is logged distinctly (e.g. WARNING/ERROR with exc_info, or chained via `raise ... from`) without replacing or swallowing the original fulfilment exception, which must still propagate afterward; add a targeted test that makes release_for_retry() fail during this handling to pin down the intended behavior.

### 2. PR-002: PaymentRepository.__init__, the timeout=30 / PRAGMA journal_mode=WAL hardening added for T1

- Location: `app/payment_service/repository.py`
- Impact: `LOW`
- Why human attention is required: This is an empirical, manual verification step (repeatedly rerunning a concurrency test while toggling production hardening) that an automated fixer cannot perform or attest to, and it concerns the correctness confidence of a concurrency/persistence mitigation -- a policy-protected category -- so it is routed to a developer rather than auto-closed.
- Requirement/plan reference: FR-3
- Original finding: The approved plan's 'Proposed design' and 'Developer verification strategy > Other deterministic checks' both explicitly call for a one-time development-time step: temporarily remove the busy-timeout/WAL configuration, rerun the concurrency test (T5) in a loop (20-50 iterations) to confirm it becomes flaky with sqlite3.OperationalError: database is locked, then restore the hardening and record this as a comment/commit note next to the hardening code. Neither repository.py nor the diff's commit history at this HEAD contains such a note; the implementer's own pr-body.md 'Deviations' section discloses this step was skipped because the implementer profile has no test-execution tool, but no later remediation round has performed or recorded it. — Without the recorded confirmation, there is no evidence in the repository that the WAL/busy-timeout hardening is actually load-bearing for FR-3/C-5's concurrency guarantee rather than redundant; a future contributor could remove or weaken it without realizing it reintroduces the sqlite3.OperationalError failure mode the plan's own 'Risks' section names, and this gap between what the plan requires and what was actually done stays invisible in the code.
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `3260103e04fc4b3ca4e5ed79eab0342eae20c215`)
- Reviewer recommendation: Either perform the documented manual flakiness check (temporarily strip the WAL/timeout hardening, run the T5 concurrency test in a loop to confirm it becomes flaky, restore it) and add the one-line comment/commit note next to the timeout=30/PRAGMA journal_mode=WAL lines, or have a developer explicitly and durably record this as an accepted, rationale-backed deviation (e.g. in pr-body.md's Deviations section) rather than leaving it silently unaddressed.

### 3. PR-003: PR HEAD SHA line and the 'Deviations' section

- Location: `.agentic-sdlc/records/PAY-DEMO-001/pr-body.md`
- Impact: `LOW`
- Why human attention is required: Correcting the stale SHA alone is a mechanical, deterministic edit, but the accurate content of the 'Deviations' section is coupled to how a developer chooses to resolve or formally accept the two open protected-category findings (PR-001, PR-002) above, so this is bundled with that unresolved judgment call rather than closed out as a standalone automatic fix.
- Requirement/plan reference: (none cited)
- Original finding: pr-body.md still states 'PR HEAD SHA: b5d765edd0a93888df13505ebbccb9d77efa5ced' (the original implementation commit) even though the branch has since advanced through two remediation commits (87f2f2c, 3260103) to the current HEAD 3260103e04fc4b3ca4e5ed79eab0342eae20c215, and its 'Deviations' section only discloses the skipped T6/manual-hardening-verification steps from the initial implementation, without mentioning the still-open release_for_retry()-failure-handling gap (PR-001 above) or the still-missing WAL flakiness-verification note (PR-002 above). — A human reviewer relying on the PR body for traceability (governance invariant 15: outputs must be traceable to the exact PR HEAD) is pointed at a stale SHA and an incomplete deviations list, understating the review package's true state and risking that the two outstanding developer-required findings are missed at final human approval.
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `3260103e04fc4b3ca4e5ed79eab0342eae20c215`)
- Reviewer recommendation: Update pr-body.md's 'PR HEAD SHA' field to the current HEAD after each remediation round, and extend its 'Deviations' section to reference the still-open release_for_retry()-failure-handling gap and the still-missing WAL/busy-timeout flakiness-verification note so the review package accurately reflects outstanding developer-required items.

## Recommended review priority

### High attention
- (none)

### Medium attention
- PR-001: process_callback(), except Exception block wrapping self._fulfilment_service.fulfil(event.payment_id), lines ~32-40

### Low-risk / mechanically verified areas
- PR-002: PaymentRepository.__init__, the timeout=30 / PRAGMA journal_mode=WAL hardening added for T1
- PR-003: PR HEAD SHA line and the 'Deviations' section

## Verification evidence

- Build: `python3 -m compileall -q app` — PASS (exit 0)
- Static/lint checks: (not run — no separate lint step configured in this version)
- Tests: `python3 -m unittest discover -t app -s app/tests -v` — PASS (exit 0)
- Other checks:
- (none)

## Residual risks / known limitations

- T6 ('run the full suite ... confirm all existing and new tests pass') was not executed by this agent: the implementer profile has no test/build execution tool, and per the delivery workflow contract, verification is performed independently by the workflow after implementation, not claimed here.
- The Proposed design's one-time manual hardening-verification step (temporarily removing T1's busy-timeout/WAL config and rerunning the T5 concurrency test in a 20-50 iteration loop to confirm it becomes flaky, then restoring the hardening) was not performed, since it requires repeated test execution and this agent has no execution tool; the hardening itself (busy timeout + WAL) was implemented in T1 as specified, but its manual empirical confirmation is left for the workflow/developer to perform and record separately, as the plan describes it as a development-time step outside the automated/CI test path.

## Reviewer decision

Final PR approval must be performed by a human in the source-control system.
