# Human Review Brief — PAY-DEMO-001 / PR (local, not yet created)

## Review target

- Jira ticket: `PAY-DEMO-001`
- Pull request: `(local, not yet created)`
- Current PR HEAD SHA: `efa6055ca0695596e220478e0c6d1df99049b7ac`
- Approved plan digest: `a1e213d5291cd2393d2798bb8b2d06d851ee068e70bdaacaf3febcc775291c89`

## Implementation summary

Implements the approved Development Plan (tasks: T1, T2, T3, T6, T4, T5, T7). Files changed: app/payment_service/repository.py, app/payment_service/payment_service.py, app/payment_service/request_handler.py, app/README.md, app/tests/test_repository.py, app/tests/test_payment_service.py, app/tests/test_callback_concurrency.py. 1 autonomous remediation round(s) applied additional fixes for findings the workflow classified as auto-eligible.

## Automated review summary

- Findings detected: 4
- Automatically remediated: 2
- Developer-remediated: 0
- Remaining/escalated: 2
- Autonomous remediation rounds: 1

## Human attention required

### 1. PR-001: whole module; plan T7 / Developer verification strategy vs supplied verification evidence

- Location: `app/tests/test_callback_concurrency.py`
- Impact: `MEDIUM`
- Why human attention is required: This finding is about missing verification evidence. The remediator cannot run commands, and editing files under the source roots cannot resolve it. It is carried over unchanged from round 1 (same finding ID).
- Requirement/plan reference: AC-1
- Original finding: The verification evidence for HEAD efa6055 has one compileall run and one full unittest run. Plan T7 and the Developer verification strategy also require: running test_callback_concurrency.py 20 times to catch SQLite lock-timeout flakiness; grep for threading/Lock in app/payment_service (CON-1/CON-2); grep for UPDATE/status in repository.py (CON-4); and an empty git diff of callback_controller.py/models.py/fulfilment_service.py against 109a0a7 (AC-2/NFR-1). None of these appear. An intermittent 'database is locked' OperationalError in the 8-thread barrier test would go unnoticed after a single passing run. — There is no evidence of the plan-mandated verification behind the NFR-2 concurrency claim or the CON-1/CON-2/CON-4/NFR-1 deterministic checks. The plan made the repeated run part of how correctness of the concurrency/transaction semantics is established.
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `efa6055ca0695596e220478e0c6d1df99049b7ac`)
- Reviewer recommendation: The workflow or a developer should run the plan's T7 verification against this HEAD: the 20-iteration concurrency loop, the grep checks and the baseline diff of the unchanged files. Record the results as verification evidence.

### 2. PR-004: PaymentRepository.release_claim

- Location: `app/payment_service/repository.py`
- Impact: `LOW`
- Why human attention is required: The code change is small, but it touches transaction semantics, which the PR review policy lists as protected. Lock-holding behaviour under contention is also hard to verify deterministically with the current tests. It is carried over unchanged from round 1 (same finding ID), because it was routed to a developer and was not in scope for the remediation round.
- Requirement/plan reference: (none cited)
- Original finding: record_if_new rolls back after any sqlite3 error so that a failed write does not keep holding the database write lock. release_claim has no matching handling. If the DELETE succeeds in taking a RESERVED lock but commit() then fails (for example 'database is locked' under contention), the connection keeps an open write transaction until close(). Through CallbackRequestHandler this ends at close() in the finally block. A long-lived PaymentRepository, like the one the existing unit tests share across calls, would keep blocking other writers, and its next record_if_new would run inside the stale transaction. — After a failed release, other requests' claim writes can stall until the busy timeout expires. They then fail as claim-write errors rather than being processed. There is no double fulfilment.
- Developer response: (pending — awaiting human review)
- Verification evidence: see "Verification evidence" below (reviewed at PR HEAD `efa6055ca0695596e220478e0c6d1df99049b7ac`)
- Reviewer recommendation: Apply the same pattern as record_if_new: on sqlite3.Error in release_claim, attempt self._conn.rollback() (suppressing any rollback error) and re-raise the original error.

## Recommended review priority

### High attention
- (none)

### Medium attention
- PR-001: whole module; plan T7 / Developer verification strategy vs supplied verification evidence

### Low-risk / mechanically verified areas
- PR-004: PaymentRepository.release_claim

## Verification evidence

- Build: `python3 -m compileall -q app` — PASS (exit 0)
- Static/lint checks: (not run — no separate lint step configured in this version)
- Tests: `python3 -m unittest discover -t app -s app/tests -v` — PASS (exit 0)
- Other checks:
- (none)

## Residual risks / known limitations

- A4 says the release error 'remains attached as the exception's __context__'. Python does not do that with the plan's sketch (bare raise) or with the permitted 'raise fulfil_error' form. It is the other way round: the release error's __context__ is the fulfilment error, and the fulfilment error that propagates has no link to the release error. The release error and its traceback are recorded only by logger.exception. I did not add code to attach it manually, because the task instructions do not ask for that. A test that asserts the __context__ link would fail.
- This worker has no shell or git access, so it did not run the parts of T7 that need them: the full suite, the 20-run concurrency loop, py_compile, grep and git diff against 109a0a7. The Python verification stage owns them.
- Carried over from T2 without changes: the plan's A4 says the release error stays attached as the propagated exception's __context__. Python links them the other way round. The release error's __context__ is the fulfilment error, and the release error is recorded only through logger.exception. No test asserts the __context__ link. I did not add code to attach it, because that would go beyond the plan's task list.
- Plan A4 says the release error 'remains attached as the exception's __context__'. Python does not produce that link with the plan's sketch or with the permitted 'raise fulfil_error' form. The release error's __context__ is the fulfilment error, and the propagated fulfilment error carries no reference to the release error, which is recorded only through logger.exception (ERROR with traceback). Adding code to attach it manually was not done because the task list does not ask for it. No test asserts this link. The rest of the HD-1/A4 default is implemented as planned: log at ERROR, re-raise the original fulfilment error, and leave the event claimed.
- The executable parts of T7 were not run by the implementer, who has no shell or git authority: the full suite, the 20-run concurrency loop, py_compile, the grep checks and 'git diff 109a0a7' on callback_controller.py, models.py and fulfilment_service.py. The workflow's Python verification stage owns them.

## Reviewer decision

Final PR approval must be performed by a human in the source-control system.
