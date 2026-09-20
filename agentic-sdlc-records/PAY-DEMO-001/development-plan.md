# Development Plan — PAY-DEMO-001

## Metadata

- Jira ticket: `PAY-DEMO-001` (Make payment callbacks idempotent)
- Repository: `awscao_learning` (`/home/dad/AIProjects/awscao_learning`; payment fixture under `app/`)
- Base branch: `main`
- Repository baseline SHA: `a806a98e5a4b024ef5000d29a3458f7c219bc068`
- Plan round: r2 (revision of the r1 plan after independent review `01-review-r1-c1.json`, status CHANGES_REQUIRED). Developer guidance was supplied and is applied (see "Assumptions and decisions").

> This plan is immutable once presented for human approval. Its SHA-256 and approval state are stored separately. Independent agent-review evidence is stored in `plan-review.json` and is bound to the SHA-256 of the exact plan content reviewed.

### Revision notes (r1 → r2)

| Reviewer finding | Disposition | How this revision addresses it |
|---|---|---|
| PLAN-001 (PLAN_CHANGE_REQUIRED): every `sqlite3.IntegrityError` was treated as a duplicate, so a NOT NULL violation on `payment_id` would be logged as a duplicate, acknowledged and not fulfilled | Addressed | `record_if_new` is now a scoped change (T1): it returns `False` only for a violation of the `provider_event_id` PRIMARY KEY and re-raises any other `sqlite3.IntegrityError`. Decision D1 (developer guidance). Tests in T4 and T5, and FR-4 traceability reworded. |
| PLAN-002 (ADVISORY): behaviour for claim rows that exist before deployment was not analysed | Addressed | New risk and assumption A10, with an operational pre-rollout check. No schema change. |
| PLAN-003 (ADVISORY): imprecise verification (T6 response equality level, C-3 grep guard, baseline red evidence) | Addressed | T6 asserts at `CallbackController` level; the C-3 guard is case-insensitive and T1 wording is constrained; new task T0 captures baseline evidence before any edit. |

Valid r1 decisions (claim-then-fulfil outside the claim transaction, delete-based release, per-request repository factory, constraint-only arbitration, unchanged controller/model) are preserved.

## Source references

### Jira
- `JIRA:PAY-DEMO-001` — "Make payment callbacks idempotent" (Description, Scope, Acceptance Criteria AC-1..AC-3).

### Confluence
- `CONF:4812` — "Payment Callback Processing" (Background, Functional requirements, Compatibility, Observability).
- `CONF:4934` — "Payment Service Architecture" (Relevant components, Transaction model, Persistence, Constraints).

### Planning artifacts
- Validated Planning Context: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-22/context/normalized/planning-context-v1.json` (no contradictions, no retrieval warnings, no blocking open questions).
- Planning Analysis: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-22/analysis/planning-analysis-v1.md`.
- Developer guidance: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-22/guidance/developer-guidance.md` (decision D1, two constraints, answer to reviewer finding r1:PLAN-001). The file describes itself as synthetic test guidance; it is applied as supplied and is subordinate to the Planning Context and governance policy.
- Previous plan and reviewer findings: `planning/history/previous-plan.md`, `planning/history/01-review-r1-c1.json`.

## Problem statement

Payment providers can send the same callback more than once, carrying a stable provider event ID (FR-1). At the baseline, `PaymentService.process_callback` (`app/payment_service/payment_service.py:23-27`) discards the boolean returned by `PaymentRepository.record_if_new` and always calls `FulfilmentService.fulfil`, so a repeated provider event fulfils again (violates AC-1) and nothing in the logs distinguishes a duplicate from a first delivery (violates AC-3).

The change must make fulfilment happen at most once per provider event ID, including under concurrent duplicates, using the persistence-layer uniqueness constraint that already exists (`provider_event_id TEXT PRIMARY KEY`), while keeping the provider-facing response unchanged. A claim whose fulfilment fails must be released so a provider retry can re-claim it (FR-3), and the repository must be constructed per request rather than shared across requests (C-4). Only a genuine uniqueness violation may be reported as a duplicate; any other persistence failure on the claim write (including other integrity errors such as NOT NULL) must be surfaced as a non-duplicate persistence failure (FR-4, D1).

## Scope

### In scope
- The payment callback processing path: `PaymentService`, `PaymentRepository`, and the tests in `app/tests/`.
- Consuming the claim result in `PaymentService` and skipping fulfilment for duplicates (FR-2, FR-5, FR-7).
- Narrowing `PaymentRepository.record_if_new` so that only a `provider_event_id` PRIMARY KEY violation is reported as "not new" (FR-4, D1).
- Releasing a claim by deleting its row when fulfilment fails after the claim, so the same provider event ID can be re-claimed through the same uniqueness constraint (FR-3, C-3).
- Per-request construction of `PaymentRepository` via an injected repository factory (C-4).
- A distinguishable duplicate log entry (AC-3, FR-8, NFR-3).
- Continuing to use the existing SQLite-backed `PaymentRepository` as the accepted PostgreSQL stand-in, as-is (C-5).
- New and adjusted unit and concurrency tests covering each of the above.

### Out of scope
- Provider adapter public interfaces (C-8). No provider adapter code exists in `app/`; none will be added or touched.
- Introducing a PostgreSQL dependency (C-5), a distributed lock service (C-1), or any new third-party dependency.
- Automatic reconciliation or timeout recovery for events left unresolved after a crash between claim and durable outcome (C-6).
- Automatic recovery of a payment whose original in-flight delivery fails after a duplicate was already acknowledged successfully (C-7).
- Any change to the external response contract or `CallbackController` behaviour (AC-2, NFR-1), including input validation of the callback body.
- A schema change or migration (no status column, no completion marker, no retention or purge of completed claims; see OQ-4 and guidance constraint).
- Payment state transitions beyond fulfilment (none exist in the repository; see OQ-3).
- Defining an error/status-code contract for failed deliveries (see OQ-1).
- Adding a composition root or HTTP framework wiring (none exists; `main.py` is an unrelated stub).
- Handling a missing or null `provider_event_id` (FR-1 states every callback carries one; see risks).

## Acceptance criteria

| ID | Criterion | Planned implementation | Planned verification |
|---|---|---|---|
| AC-1 | Repeated callbacks with the same provider event ID must not cause duplicate fulfilment. | T2: `PaymentService.process_callback` uses the boolean from `record_if_new`; when `False` it skips `fulfil()`. The uniqueness constraint on `provider_event_id` (existing PRIMARY KEY) is the sole arbiter (FR-6, NFR-2). T1 makes `record_if_new` report `False` only for a violation of that constraint and adds the release path so a failed claimant does not permanently block retries (FR-3). | Existing `test_duplicate_callback_does_not_refulfil` (T3 keeps it passing after the wiring change; T0 records it failing on the baseline); T5 release-then-retry test; T6 concurrent-duplicate test asserts fulfilment count is exactly 1 across N threads sharing one `CallbackController`/`PaymentService` and separate per-request connections; T4 asserts exactly one `record_if_new` returns `True` under concurrency. |
| AC-2 | Existing provider-facing HTTP response behaviour must remain backward compatible. | `CallbackController` is not modified. Its response `{"status": "ok", "payment_id": result.payment_id}` is independent of `ProcessingResult.fulfilled`. `PaymentService` returns `ProcessingResult(payment_id=event.payment_id, fulfilled=False)` for duplicates, so the duplicate response has the same keys and values as a first delivery. Failure of a fulfilment still propagates as an exception exactly as today (no new error handling). One input-dependent consequence of FR-4/D1 is called out in A9 and Risks (a callback with a null `payment_id`). | Existing `test_response_shape_is_identical_for_new_and_duplicate` unchanged in assertions; T5 adds an in-flight-duplicate response-equality test at controller level (FR-7) and a test that a fulfilment failure still propagates out of `CallbackController.handle` unchanged; T7 confirms `git diff` shows `callback_controller.py` and `models.py` untouched. |
| AC-3 | Duplicate callback attempts must be observable in application logs. | T2: on a rejected claim (a genuine uniqueness violation only, per D1), `PaymentService` logs at INFO on logger `payment_service.payment_service` a message that contains the word `duplicate` and the `provider_event_id` (and `payment_id`). The first-delivery message remains `callback processed ...` and does not contain `duplicate`. A non-uniqueness persistence failure is never logged as a duplicate. | Existing `test_duplicate_is_distinguishable_in_logs` (`assertLogs`, INFO; T0 records it failing on the baseline); T5 asserts the duplicate entry contains the event ID, that a first delivery emits no `duplicate` entry, and that a NOT NULL claim failure emits no `duplicate` entry. |

### Requirement, constraint and limitation traceability

| Item | How the plan satisfies or treats it |
|---|---|
| FR-1 | Relies on the stable `provider_event_id` as the constraint key; no code needed. |
| FR-2 | T2: duplicate skips fulfilment. Completed events keep their row indefinitely, so the constraint rejects any later duplicate. |
| FR-3 | T1 `release_claim` (row delete) + T2 `except` around `fulfil()` releases the claim and re-raises; a retry re-claims through the same `record_if_new` INSERT. T5 verifies fail-then-retry fulfils exactly once. |
| FR-4 | A claim-write failure that is not a `provider_event_id` uniqueness violation is a non-duplicate persistence failure. This covers other `sqlite3` errors (e.g. `OperationalError`) and, per D1, any other `sqlite3.IntegrityError` such as NOT NULL on `payment_id`. `record_if_new` re-raises all of them; the service propagates them before any claim exists, so no release is attempted, fulfilment is not called and no `duplicate` log is emitted. T1 implements the discrimination, T4 tests it at repository level (NULL `payment_id` raises, genuine duplicate still returns `False`), T5 tests it at service level. |
| FR-5, FR-7 | The claim is committed before fulfilment starts, so a concurrent duplicate gets the PRIMARY KEY `IntegrityError` immediately, skips fulfilment and receives the same response. T5 (deterministic in-flight test with a blocking fulfilment double) and T6. |
| FR-6, NFR-2, C-3 | Every idempotency write (first claim, and re-claim after failure) is an `INSERT` under the `provider_event_id` PRIMARY KEY; release is a `DELETE` of that row. Duplicate detection is decided by the database constraint's own error, with no SELECT-before-INSERT pre-check and no lock (D1). There is no status column and no conditional `UPDATE`. T7 adds a case-insensitive grep guard on `repository.py`. |
| FR-8, NFR-3 | See AC-3. |
| NFR-1 | See AC-2. |
| C-1 | No lock of any kind is added. |
| C-2, C-5 | Existing `sqlite3` repository, table and transaction handling reused; stdlib only; no schema migration. |
| C-4 | T2: `PaymentService` takes a `repository_factory` and builds a fresh `PaymentRepository` (fresh connection to the same database) for each `process_callback` call and closes it in `finally`. T5 verifies one construction and one close per call; T6 shares a single `PaymentService` across threads. |
| C-6 | Not mitigated (accepted). The claim-then-fulfil order retains the accepted crash window. |
| C-7 | Not mitigated (accepted). T5 includes a test that documents the behaviour (duplicate acknowledged, original fails, claim released) without asserting any automatic recovery. |
| C-8 | No provider adapter interface exists or is changed; verified by diff scope in T7. |

## Assumptions and decisions

Developer guidance applied (each item cited where it is used):

- **D1 (developer guidance, decision D1: non-uniqueness integrity errors).** `record_if_new` returns `False` only for a violation of the `provider_event_id` PRIMARY KEY. Any other `sqlite3.IntegrityError` (e.g. NOT NULL on `payment_id`) propagates and is treated as a non-duplicate persistence failure under FR-4; it is never logged as a duplicate. The database constraint stays the sole arbiter: no SELECT-before-INSERT pre-check and no lock. Implemented in T1, tested in T4 and T5.
- **G-C1 (developer guidance, constraint: Python 3.10).** `sqlite3.Error.sqlite_errorcode` is not available, so the uniqueness violation is recognised by inspecting the error message for `UNIQUE constraint failed: processed_callbacks.provider_event_id`. The plan does not add a version-conditional branch to use `sqlite_errorcode` on newer runtimes.
- **G-C2 (developer guidance, constraint: no new dependency, no schema migration).** Stdlib only; the `processed_callbacks` table, PRIMARY KEY and `NOT NULL` are unchanged.
- **G-R1 (developer guidance, answer to reviewer finding r1:PLAN-001).** Handled as a scoped change to `record_if_new` with a test that a null `payment_id` raises rather than being reported as a duplicate; the FR-4 traceability wording is corrected (see traceability table). No conflict with the Planning Context or governance was found in any guidance item.

Assumptions (each is non-blocking and is recorded so the reviewer and approver can challenge it):

- **A1 (OQ-1, response values).** The repository has no HTTP layer; the "response" is the dict returned by `CallbackController.handle`. "Unchanged" is interpreted as: that dict for a first delivery and for a duplicate stays exactly `{"status": "ok", "payment_id": <event payment_id>}`, and a failing delivery keeps propagating an exception as today. No status code or failure-body is invented.
- **A2 (OQ-2, log content).** No format is specified. The plan uses INFO level, logger `payment_service.payment_service` (already pinned by the existing test), and a message `duplicate callback ignored provider_event_id=%s payment_id=%s`. Release of a claim after a failed fulfilment is logged separately (see design). Exact wording is an implementation choice within those bounds.
- **A3 (OQ-5, mandatory vs advisory).** AC-3 says "must", CONF:4812 says "should". The plan treats logging as mandatory (the stricter reading), which satisfies both.
- **A4 (OQ-3, scope beyond fulfilment).** The repository performs no payment state transitions; "payment processing" is only claim + fulfilment. The design gates exactly that.
- **A5 (OQ-4, "completed" and retention).** "Completed business processing" is taken to mean: the claim row exists and was not released. A claim row is retained indefinitely; no retention or purge is designed. In-flight and completed events are deliberately indistinguishable in storage, which is sufficient for FR-2 and FR-5 through the constraint alone, and avoids a schema change.
- **A6 (same event ID, different `payment_id`).** Not specified by any source. The first claim wins; the repository continues to ignore a later `payment_id`. A duplicate's response echoes the `payment_id` in its own body, matching the current response construction and the existing test.
- **A7 (release failure).** If `release_claim` itself raises while handling a fulfilment failure, the plan logs it, re-raises the original fulfilment exception, and leaves the claim stuck. This is treated as within the C-6 accepted limitation (an outcome not durably recorded); the sources do not explicitly cover it, so it is called out here and in Risks.
- **A8 (wiring change).** Satisfying C-4 requires changing the `PaymentService` constructor (repository instance replaced by a repository factory). `PaymentService` is an internal component, not a provider adapter, so C-8 is not affected. This is a material design choice under the governance policy (transaction/concurrency semantics) and is surfaced for the approver.
- **A9 (malformed `payment_id`, consequence of D1/FR-4).** At the baseline a callback with a null `payment_id` fails the claim INSERT (NOT NULL), the failure is swallowed as if it were a duplicate, and `fulfil()` still runs and the normal `ok` response is returned. That accidental behaviour is not a documented provider contract (OQ-1). After this change the same input raises out of `record_if_new` and propagates from `CallbackController.handle` like any other persistence failure (FR-4, D1), with no fulfilment. The plan treats this as required by FR-4 and D1 rather than as a relaxation of AC-2, because the sources define the "unchanged" contract only for first deliveries and duplicates. It is flagged here and in Risks so the approver can confirm.
- **A10 (claim rows that exist before deployment; review finding PLAN-002).** At the baseline a claim row is committed before `fulfil()` and never released. After deployment, a provider retry for an event whose row already exists is a duplicate and is suppressed, including any event whose fulfilment had in fact failed before the deployment (previously a retry would have re-fulfilled it). Rows written before the change cannot be told apart from completed events (no status column, and none is added). This is accepted as part of the existing "row present means processed" semantics (A5), and is related to but not the same as C-6; it is called out for the approver in Risks together with an operational check.
- **A11.** Only failures of the claim write are classified by the message check. `release_claim` and connection construction errors are not classified and propagate as ordinary exceptions.

Unresolved human decisions: none blocking. OQ-1 to OQ-5 remain open in the Planning Context as non-blocking and are handled by A1 to A5. A8, A9 and A10 are approver-visible design/behaviour consequences rather than open questions. If the approver wants a different failure response, log format, retention behaviour, or a different treatment of malformed callbacks, the plan needs revision before implementation.

## Repository / impact analysis

- Affected components:
  - `app/payment_service/payment_service.py` (primary: claim branching, release on failure, logging, per-request repository).
  - `app/payment_service/repository.py` (narrowed `record_if_new` error handling; new `release_claim`; docstring).
  - `app/tests/test_payment_service.py` (setUp wiring changed to the factory; existing assertions kept).
  - New test modules under `app/tests/` (repository, failure/log/response behaviour, concurrency).
  - Unchanged: `callback_controller.py`, `fulfilment_service.py`, `models.py`, `__init__.py`.
- Existing patterns to reuse:
  - Layering Controller → Service → Repository/Fulfilment with constructor injection, `from __future__ import annotations`, docstrings citing ticket and AC ids.
  - Repository style: raw `sqlite3`, one method per operation, commit inside the method, integrity error translated to a boolean.
  - Logging style: module logger with lazy `%s` formatting.
  - Tests: `unittest`, per-test temp directory with a file-backed SQLite database (required so that separate connections share one database; `:memory:` would give each connection its own database).
- Dependencies: Python standard library only (`sqlite3`, `logging`, `threading`, `unittest`, `tempfile`). No manifest or dependency change. Runtime is Python 3.10 (`__pycache__` shows `cpython-310`; guidance G-C1).
- Existing behaviour relied on: `sqlite3.connect` default `check_same_thread=True` (so a repository cannot be shared across request threads anyway) and the default 5-second busy timeout on file databases. These are general `sqlite3` module defaults, not repository facts, and are verified indirectly by the concurrency tests. The exact text of SQLite's uniqueness-violation message is likewise an assumption of the runtime, pinned by tests (see design).

## Proposed design

### Order of operations (claim, then fulfil, outside the claim transaction)

```
process_callback(event):
    repo = self._repository_factory()          # fresh connection per request (C-4)
    try:
        if not repo.record_if_new(event.provider_event_id, event.payment_id):
            # False means a provider_event_id uniqueness violation only (D1)
            logger.info("duplicate callback ignored provider_event_id=%s payment_id=%s", ...)
            return ProcessingResult(payment_id=event.payment_id, fulfilled=False)
        try:
            self._fulfilment_service.fulfil(event.payment_id)
        except Exception:
            self._release_after_failure(repo, event)   # DELETE claim row; log; never masks the original error
            raise
        logger.info("callback processed for provider_event_id=%s", event.provider_event_id)
        return ProcessingResult(payment_id=event.payment_id, fulfilled=True)
    finally:
        repo.close()
```

- **Claim and duplicate discrimination (FR-4, D1, G-C1).** `record_if_new` remains a single `INSERT` that commits immediately, returning `True` on success. On `sqlite3.IntegrityError` it decides from the error itself, with no pre-check query:
  - if the message contains the module constant `UNIQUE constraint failed: processed_callbacks.provider_event_id`, it rolls back the failed statement and returns `False`;
  - otherwise (e.g. `NOT NULL constraint failed: processed_callbacks.payment_id`) it rolls back and re-raises the original exception.

  Other `sqlite3` errors are not caught, as today. Because the constraint remains the sole arbiter (no SELECT-before-INSERT, no lock), concurrent inserts still genuinely race at the persistence layer (NFR-2, FR-6). The rollback releases the implicit transaction that Python's `sqlite3` leaves open after a failed DML statement, so the connection is left clean and holds no write lock; it does not change any result.
- **Failure direction of the message check.** If a different SQLite build worded the message differently, a genuine duplicate would be re-raised instead of returned as `False`. That fails loudly (an exception, never a second fulfilment) and the pinned repository tests (T4) would fail in that environment, so the check cannot silently misclassify in the unsafe direction. Wording is not assumed stable across all SQLite versions; it is verified by test on the target runtime.
- **Fulfilment is deliberately not run inside the claim transaction.** Holding a write transaction across `fulfil()` would make a concurrent duplicate on a file SQLite database block on the write lock up to the busy timeout and then raise `OperationalError` (not `IntegrityError`), which would break FR-5 and FR-7. Committing the claim first avoids this and matches the existing structure.
- **Non-duplicate claim failure (FR-4).** Any exception from `record_if_new` (a non-uniqueness `IntegrityError` such as NOT NULL, or any other `sqlite3` error) propagates out of the outer `try` (only `finally: close()` runs). No claim exists, so nothing is released, fulfilment is not called, no `duplicate` line is logged, and no released-claim bookkeeping is needed. The provider's ordinary retry repeats the same claim attempt.
- **Release (FR-3, C-3).** A new `PaymentRepository.release_claim(provider_event_id)` executes `DELETE FROM processed_callbacks WHERE provider_event_id = ?` and commits. The retry then re-inserts through the same `record_if_new` `INSERT` under the same PRIMARY KEY, i.e. delete-then-reinsert under the constraint. There is no status column, no `UPDATE` and no compare-and-swap.
- **Release ownership.** `release_claim` is only reachable from the inner `except` that runs after this request's own claim returned `True`. A rejected duplicate, or a failed claim write, returns/raises before that point, so it can never delete a row it did not create. The release runs at most once per request and is not retried.
- **Release failure (A7).** `_release_after_failure` wraps `release_claim` in `try/except Exception`, logs the release failure at ERROR with the event ID, and the caller re-raises the original fulfilment exception (the release error is not allowed to replace it). A log entry is also emitted when a release succeeds (WARNING, event ID, "claim released after fulfilment failure"), which gives operations visibility into retried events. These extra log lines contain neither the word `duplicate` nor change any response.
- **Exception scope.** The inner handler catches `Exception` (not `BaseException`); process-level interrupts are treated like a crash and fall under C-6.

### Per-request repository (C-4)

- `PaymentService.__init__(self, repository_factory: Callable[[], PaymentRepository], fulfilment_service: FulfilmentService)` replaces the instance parameter. Each `process_callback` call calls the factory once, and closes the returned repository in `finally`.
- A factory keeps dependency injection consistent with the existing style, avoids hard-coding a database path in the service, and keeps `PaymentRepository`'s constructor `(db_path)` unchanged. In the tests the factory is `lambda: PaymentRepository(db_path)`. Because no composition root exists in the repository, production wiring is out of scope; any future composition root only needs to pass a factory that opens a fresh connection to the same store.
- `PaymentRepository.__init__` still runs `CREATE TABLE IF NOT EXISTS` and `commit()` on every construction. Under per-request construction this repeats on each request. It is a no-op once the table exists and no schema change is planned. Whether it causes lock contention under concurrency is an inference, not a verified fact, so T6 exercises it. If T6 shows contention, the contingency is to move schema creation out of per-request construction, and that contingency would be raised as a plan deviation rather than applied silently.
- `CallbackController` continues to receive one `PaymentService` instance; that instance holds no connection, so sharing it across concurrent requests is safe.

### Response and logging

- Duplicate path returns `ProcessingResult(payment_id=event.payment_id, fulfilled=False)`. The controller already ignores `fulfilled`, so the external response is the same dict as for a first delivery (AC-2, FR-7). Note that at `PaymentService` level `fulfilled` differs (`True` for the winner, `False` for duplicates); response equality is therefore asserted at controller level in the tests.
- The duplicate INFO message uses lazy `%s` formatting like the existing line, on the existing module logger.

### Alternatives considered

- **Conditional `UPDATE ... WHERE status='failed'` for retry:** rejected; explicitly forbidden by C-3.
- **Fulfilment inside the claim transaction:** rejected (FR-5/FR-7 risk above).
- **Application lock or lock service:** rejected (C-1, FR-6).
- **SELECT-before-INSERT to tell duplicate from other errors:** rejected (D1: the constraint stays the sole arbiter; a pre-check would also add a race window).
- **Treat non-uniqueness `IntegrityError` as an accepted limitation instead of discriminating:** rejected by D1; it would log a malformed callback as a duplicate and pollute the AC-3 signal.
- **Use `sqlite_errorcode` to discriminate:** not available on Python 3.10 (G-C1).
- **Service opens `PaymentRepository(db_path)` itself:** rejected in favour of a factory (hard-coded path, harder to test failure injection).
- **Add a status column:** rejected (schema migration for no requirement; `CREATE TABLE IF NOT EXISTS` would not alter an existing table; C-3; G-C2).

## Implementation tasks

| Task | Goal | Depends on | Parallelizable |
|---|---|---|---|
| T0 | Baseline evidence, before any file is edited: on the unmodified baseline, run `python3 -m unittest discover -t app -s app/tests -v` and save the full output (in the implementation record or PR description, not in the repository tree). Expected from code reading, to be confirmed by this run: `test_duplicate_callback_does_not_refulfil` and `test_duplicate_is_distinguishable_in_logs` fail, the other two pass. This is the red evidence for AC-1 and AC-3 that cannot be reproduced after T3 rewrites `setUp`. Maps to AC-1, AC-3. | - | No (must precede T1-T3) |
| T1 | `repository.py`: (a) change `record_if_new` so a `sqlite3.IntegrityError` returns `False` only when its message contains a module-level constant `UNIQUE constraint failed: processed_callbacks.provider_event_id`; any other `IntegrityError` is re-raised unchanged; call `rollback()` on the connection before returning `False` or re-raising; no SELECT-before-INSERT, no lock, other `sqlite3` errors still propagate (FR-4, D1, G-C1); (b) add `release_claim(provider_event_id)` (`DELETE` + `commit`, no return value) (FR-3, C-3); (c) update the module docstring to describe claim/release under the uniqueness constraint and that this is not a lock, and to document that only a `provider_event_id` uniqueness violation counts as a duplicate. Do not change the schema or the constructor. Wording constraint for docstrings and comments: avoid the words `update`, `status`, `upsert`, `replace` and `on conflict` so the T7 guard stays meaningful. | T0 | Yes (with T4 test authoring once the signatures are agreed) |
| T2 | `payment_service.py`: replace the repository parameter with `repository_factory`; per-request construct/close; branch on the `record_if_new` result (duplicate: INFO log with `duplicate`, return `fulfilled=False`); let non-duplicate claim failures propagate untouched; wrap `fulfil()` so a failure releases the claim (via `release_claim`), logs, and re-raises the original exception; remove the "Known gap" docstring and cite PAY-DEMO-001 behaviour instead. Maps to AC-1, AC-3, FR-2..FR-5, FR-7, C-4. | T1 | No |
| T3 | `app/tests/test_payment_service.py`: change `setUp`/`tearDown` to build the service with a `PaymentRepository` factory over the temp-dir database (no long-lived repository to close). Keep the four existing tests' assertions unchanged. | T2 | No |
| T4 | New `app/tests/test_repository.py`: (a) `release_claim` deletes the row; `record_if_new` returns `True` again after release; releasing an unknown event ID is a no-op; (b) a genuine duplicate insert returns `False` (this pins the message constant against the runtime's actual SQLite wording); (c) `record_if_new("evt", None)` raises `sqlite3.IntegrityError` instead of returning `False`, and no row is stored for it (FR-4, D1); (d) after that NOT NULL failure the same repository can still record another event, and a second connection can write to the database (no stuck transaction or lock, exercising the rollback); (e) multiple threads, each with its own `PaymentRepository` over one file database, racing on one event ID produce exactly one `True`. Maps to FR-3, FR-4, FR-6, NFR-2, C-3. | T1 | Yes (with T2, T3) |
| T5 | New `app/tests/test_payment_idempotency.py` (service/controller level, single-threaded where possible): (a) fulfilment failure releases the claim and a retry with the same event ID fulfils exactly once; (b) the failure exception propagates unchanged out of `CallbackController.handle`; (c) a non-uniqueness claim failure is not a duplicate: both a non-`IntegrityError` `sqlite3` error from `record_if_new` (via a repository double) and a real NOT NULL `IntegrityError` (callback with `payment_id` null against a real file database) propagate, fulfilment is not called, `release_claim` is not called, and no log record containing `duplicate` is emitted (FR-4, D1); (d) deterministic in-flight duplicate using a blocking fulfilment double (`threading.Event`, with timeouts so a defect fails rather than hangs): the duplicate returns, at controller level, the same response dict as a first delivery and does not fulfil; (e) C-7 documenting test: after (d) the original fails, the claim is released and no automatic re-delivery is asserted; (f) a release failure does not mask the original exception; (g) duplicate log entry contains `duplicate` and the event ID, and a first delivery emits no `duplicate` log; (h) one repository is constructed and closed per call (C-4). Maps to AC-1..AC-3, FR-3..FR-5, FR-7, C-4, C-7, D1. | T2, T3 | Yes (with T6) |
| T6 | New `app/tests/test_callback_concurrency.py`: one shared `CallbackController` over one `PaymentService` with a factory over a file database; N threads released together with `threading.Barrier`, all calling `CallbackController.handle` with the same `provider_event_id`; assert fulfilment count is exactly 1, every controller response dict is equal (the `ProcessingResult.fulfilled` flag legitimately differs between the winner and duplicates at service level, so equality is asserted only on the controller response), and all threads finished within a timeout. Repeat over several distinct event IDs per test run to increase the chance of real overlap. Also serves as the check on per-request DDL contention. Maps to AC-1, AC-2, FR-5, FR-6, FR-7, NFR-2, C-4. | T2, T3 | Yes (with T5) |
| T7 | Final verification: run the full suite and the deterministic checks listed below; confirm the diff touches only the files named in this plan; confirm docstrings are consistent with the new behaviour. | T1-T6 | No |

### Dependencies and parallelisation

- Critical path: T0 → T1 → T2 → T3 → (T5 ∥ T6) → T7.
- T0 must complete before any repository file is edited, because T3 rewrites the fixture the baseline evidence depends on.
- T4 needs only the T1 interface (`record_if_new(...) -> bool` with the D1 semantics, and `release_claim(provider_event_id) -> None`, both fixed by this plan) and can proceed in parallel with T2 and T3.
- T5 and T6 are in separate files and are independent of each other once T2 and T3 are complete.
- T1 and T2 edit different files, but T2 depends on the T1 semantics of `record_if_new` and on `release_claim`, so they are sequenced to avoid integration guesswork.
- Because the test files share no state, parallel test authoring has no merge conflict beyond the `setUp` factory pattern introduced in T3, which T5 and T6 should copy rather than import.

## Developer verification strategy

- **Build/static checks:**
  - `python3 -m compileall -q app` (syntax/import sanity; the project has no configured linter or type checker).
  - Grep guard for C-3, case-insensitive: `grep -niE "\b(update|status|upsert|replace)\b|on conflict" app/payment_service/repository.py` must return nothing (T1 keeps docstrings and comments free of these words, so a hit indicates a real status or compare-and-swap style write). Manual confirmation that the only SQL statements in the file are `CREATE TABLE IF NOT EXISTS`, `INSERT`, and `DELETE`.
  - Grep guard for C-1/C-5: no new imports of locking, network or database packages in `app/payment_service/` (only `sqlite3`, `logging`, `pathlib`, `typing`, `collections.abc`/`__future__`), and `record_if_new` contains no `SELECT`.
- **Unit tests:** `python3 -m unittest discover -t app -s app/tests -v` (command from `app/README.md`). Red-to-green evidence: T0's saved baseline run (original test file, before T1-T3) is expected to show `test_duplicate_callback_does_not_refulfil` and `test_duplicate_is_distinguishable_in_logs` failing; after T2/T3 the four existing tests pass, together with the new tests in T4 and T5. The baseline failure expectation is inferred from code reading until T0 runs it; T0's actual output is the evidence, and any difference from the expectation is reported rather than smoothed over.
- **Integration/concurrency tests:** T6, and the threaded case in T4, against a real file-backed SQLite database with separate connections per request. Run the concurrency module several times (for example `for i in 1 2 3 4 5; do python3 -m unittest discover -t app -s app/tests -p 'test_callback_concurrency.py'; done`) to look for flakiness, since a race test can pass by chance. Assertions must depend on the outcome being exactly-one, not on a timing window.
- **Other deterministic checks:**
  - `git diff --stat` shows changes limited to `payment_service.py`, `repository.py`, `test_payment_service.py` and the new test modules; `callback_controller.py`, `models.py` and `fulfilment_service.py` are unchanged (AC-2, C-8).
  - No `.db` file or other artefact is left in the repository by the tests (temp directories are cleaned up in `tearDown`).
- **Not verifiable in this repository:** behaviour against a real PostgreSQL instance and a real provider (out of scope per C-5, C-8), the exact production HTTP status codes (OQ-1), and SQLite error wording on runtimes other than the one the tests run on. The plan makes no claim about those.

## Risks and compatibility / rollout considerations

- **Compatibility (API/contract):** the external response dict and `ProcessingResult` are unchanged in shape. `CallbackController` and `models.py` are untouched. The only signature change is the internal `PaymentService` constructor (A8); any code outside this repository that constructs `PaymentService` with a repository instance would break. No such caller exists in the repository (wiring exists only in tests). This is a material, approver-visible change.
- **Behaviour change for a malformed callback (A9, D1):** a callback whose `payment_id` is null used to be acknowledged with `ok` (and fulfilled with a null payment ID) because the failed claim INSERT was swallowed as a "duplicate". It now propagates a persistence exception with no fulfilment and no duplicate log. This follows FR-4 and D1 but is an observable change for that input; the approver should confirm it is acceptable.
- **Persistence/schema:** none. Same table, same PRIMARY KEY, same file, no migration (G-C2). Existing databases and existing rows remain structurally valid.
- **Claim rows that exist before deployment (A10, PLAN-002):** rows written by the old code are treated as "already processed", so their duplicates are suppressed. This includes any event whose fulfilment failed before the deployment: previously a provider retry would have re-fulfilled it, now it is silently treated as a duplicate and never retried. The old code never released a claim and stored no outcome, so such rows cannot be identified from `processed_callbacks` alone. Recommended operational check for the approver before rollout: reconcile the claim table against the fulfilment records (or the provider's delivery dashboard) for events that were claimed but not fulfilled, and remediate them manually if any exist. No schema change is implied by this check, and no automatic recovery is in scope (compare C-6).
- **Rollout / rollback:** no feature flag is needed; the change is confined to the callback path. Rollback is a code revert with no data cleanup, because claim rows written by the new code are of the same shape the old code writes. Rows released by the new code are simply absent. One behavioural note: during a rolling deployment where old and new instances share one database, an old instance still re-fulfils duplicates, so the guarantee holds only once all instances run the new code.
- **Security/authorization:** no change to authentication or authorization. Log lines carry `provider_event_id` and `payment_id`, which the existing log line already carries (event ID); `payment_id` is newly logged on the duplicate path, and no payment credentials or amounts are involved. Reviewer to confirm that `payment_id` in logs is acceptable.
- **Operational/observability:** new INFO duplicate line, WARNING on claim release after a fulfilment failure, ERROR if the release itself fails. Non-duplicate claim failures surface as propagated exceptions, not as duplicate lines, so the duplicate log stays a clean signal (AC-3). Log level policy is undefined in the repository (A2).
- **Accepted limitations (not mitigated):**
  - C-6: crash after claim but before the outcome is durably recorded leaves the event permanently claimed with no automatic recovery.
  - C-7: a duplicate that arrived while the original was in flight receives a success acknowledgement; if the original then fails and is released, no further callback for that event ID will arrive and the payment is not recovered.
  - A7 (release failure) is treated as a variant of C-6 and stays stuck.
- **Technical risks:**
  - *Message-based uniqueness detection (D1, G-C1):* depends on SQLite's error wording and on the table and column names in the message. Fails toward raising, never toward a second fulfilment; pinned by T4(b) and T5(c). If the table or column is ever renamed, the constant must change with it.
  - *Wrong owner releasing a claim:* mitigated by structure (release only after this request's own successful claim); covered by tests T5(a), T5(d), T5(e).
  - *Lock contention from per-request `CREATE TABLE IF NOT EXISTS`:* unverified inference; T6 exercises it; contingency described above would be a flagged deviation.
  - *Flaky or vacuous concurrency tests:* mitigated by the deterministic in-flight test (T5d), a repository-level exactly-one-winner test (T4e) that does not depend on fulfilment timing, repeated runs, and timeouts on every wait.
  - *Same event ID with a different `payment_id`:* behaviour unspecified (A6); first claim wins.
  - *Missing or null `provider_event_id`:* out of scope (FR-1 says every callback carries one). Note that SQLite permits NULL in a non-integer `PRIMARY KEY` column, so a null event ID would insert without a uniqueness violation and would not be deduplicated; no validation is added because that would change controller behaviour (AC-2). Flagged for the approver; a follow-up ticket could add input validation.
  - *Existing-test edits:* T3 changes only the wiring in `setUp`/`tearDown`; the assertions of existing tests are not weakened.

## Independent plan review

Final independent review evidence is maintained in the sibling `plan-review.json` artifact and is cryptographically bound to this plan's SHA-256. The plan itself is not modified after the passing review merely to embed the review result.
