# Development Plan — PAY-DEMO-001

## Metadata

- Jira ticket: `PAY-DEMO-001` (Make payment callbacks idempotent)
- Repository: `awscao_learning` (`/home/dad/AIProjects/awscao_learning`; payment fixture under `app/`)
- Base branch: `main`
- Repository baseline SHA: `a806a98e5a4b024ef5000d29a3458f7c219bc068`
- Plan round: r1 (first draft). No previous plan, no reviewer findings and no developer guidance file were supplied.

> This plan is immutable once presented for human approval. Its SHA-256 and approval state are stored separately. Independent agent-review evidence is stored in `plan-review.json` and is bound to the SHA-256 of the exact plan content reviewed.

## Source references

### Jira
- `JIRA:PAY-DEMO-001` — "Make payment callbacks idempotent" (Description, Scope, Acceptance Criteria AC-1..AC-3).

### Confluence
- `CONF:4812` — "Payment Callback Processing" (Background, Functional requirements, Compatibility, Observability).
- `CONF:4934` — "Payment Service Architecture" (Relevant components, Transaction model, Persistence, Constraints).

### Planning artifacts
- Validated Planning Context: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-21/context/normalized/planning-context-v1.json` (no contradictions, no retrieval warnings, no blocking open questions).
- Planning Analysis: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-21/analysis/planning-analysis-v1.md`.

## Problem statement

Payment providers can send the same callback more than once, carrying a stable provider event ID (FR-1). At the baseline, `PaymentService.process_callback` (`app/payment_service/payment_service.py:23-27`) discards the boolean returned by `PaymentRepository.record_if_new` and always calls `FulfilmentService.fulfil`, so a repeated provider event fulfils again (violates AC-1) and nothing in the logs distinguishes a duplicate from a first delivery (violates AC-3).

The change must make fulfilment happen at most once per provider event ID, including under concurrent duplicates, using the persistence-layer uniqueness constraint that already exists (`provider_event_id TEXT PRIMARY KEY`), while keeping the provider-facing response unchanged. A claim whose fulfilment fails must be released so a provider retry can re-claim it (FR-3), and the repository must be constructed per request rather than shared across requests (C-4).

## Scope

### In scope
- The payment callback processing path: `PaymentService`, `PaymentRepository`, and the tests in `app/tests/`.
- Consuming the claim result in `PaymentService` and skipping fulfilment for duplicates (FR-2, FR-5, FR-7).
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
- Any change to the external response contract or `CallbackController` behaviour (AC-2, NFR-1).
- A schema change (no status column, no completion marker, no retention or purge of completed claims; see OQ-4).
- Payment state transitions beyond fulfilment (none exist in the repository; see OQ-3).
- Defining an error/status-code contract for failed deliveries (see OQ-1).
- Adding a composition root or HTTP framework wiring (none exists; `main.py` is an unrelated stub).

## Acceptance criteria

| ID | Criterion | Planned implementation | Planned verification |
|---|---|---|---|
| AC-1 | Repeated callbacks with the same provider event ID must not cause duplicate fulfilment. | T2: `PaymentService.process_callback` uses the boolean from `record_if_new`; when `False` it skips `fulfil()`. The uniqueness constraint on `provider_event_id` (existing PRIMARY KEY) is the sole arbiter (FR-6, NFR-2). T1 adds the release path so a failed claimant does not permanently block retries (FR-3). | Existing `test_duplicate_callback_does_not_refulfil` (T3 keeps it passing after the wiring change); T5 release-then-retry test; T6 concurrent-duplicate test asserts fulfilment count is exactly 1 across N threads sharing one `PaymentService` and separate per-request connections; T4 asserts exactly one `record_if_new` returns `True` under concurrency. |
| AC-2 | Existing provider-facing HTTP response behaviour must remain backward compatible. | `CallbackController` is not modified. Its response `{"status": "ok", "payment_id": result.payment_id}` is independent of `ProcessingResult.fulfilled`. `PaymentService` returns `ProcessingResult(payment_id=event.payment_id, fulfilled=False)` for duplicates, so the duplicate response has the same keys and values as a first delivery. Failure of a fulfilment still propagates as an exception exactly as today (no new error handling). | Existing `test_response_shape_is_identical_for_new_and_duplicate` unchanged in assertions; T5 adds an in-flight-duplicate response-equality test (FR-7) and a test that a fulfilment failure still propagates out of `CallbackController.handle` unchanged; T7 confirms `git diff` shows `callback_controller.py` and `models.py` untouched. |
| AC-3 | Duplicate callback attempts must be observable in application logs. | T2: on a rejected claim, `PaymentService` logs at INFO on logger `payment_service.payment_service` a message that contains the word `duplicate` and the `provider_event_id` (and `payment_id`). The first-delivery message remains `callback processed ...` and does not contain `duplicate`. | Existing `test_duplicate_is_distinguishable_in_logs` (`assertLogs`, INFO); T5 asserts the duplicate entry contains the event ID and that a first delivery emits no `duplicate` entry. |

### Requirement, constraint and limitation traceability

| Item | How the plan satisfies or treats it |
|---|---|
| FR-1 | Relies on the stable `provider_event_id` as the constraint key; no code needed. |
| FR-2 | T2: duplicate skips fulfilment. Completed events keep their row indefinitely, so the constraint rejects any later duplicate. |
| FR-3 | T1 `release_claim` (row delete) + T2 `except` around `fulfil()` releases the claim and re-raises; a retry re-claims through the same `record_if_new` INSERT. T5 verifies fail-then-retry fulfils exactly once. |
| FR-4 | A non-duplicate persistence error in `record_if_new` (anything other than `sqlite3.IntegrityError`) propagates before the claim exists; no release is attempted. T5 verifies no fulfilment and no `release_claim` call. |
| FR-5, FR-7 | The claim is committed before fulfilment starts, so a concurrent duplicate gets `IntegrityError` immediately, skips fulfilment and receives the same response. T5 (deterministic in-flight test with a blocking fulfilment double) and T6. |
| FR-6, NFR-2, C-3 | Every idempotency write (first claim, and re-claim after failure) is an `INSERT` under the `provider_event_id` PRIMARY KEY; release is a `DELETE` of that row. There is no status column and no conditional `UPDATE`. T7 adds a grep check that `repository.py` contains no `UPDATE` and no `status`. |
| FR-8, NFR-3 | See AC-3. |
| NFR-1 | See AC-2. |
| C-1 | No lock of any kind is added. |
| C-2, C-5 | Existing `sqlite3` repository, table and transaction handling reused; stdlib only. |
| C-4 | T2: `PaymentService` takes a `repository_factory` and builds a fresh `PaymentRepository` (fresh connection to the same database) for each `process_callback` call and closes it in `finally`. T5 verifies one construction and one close per call; T6 shares a single `PaymentService` across threads. |
| C-6 | Not mitigated (accepted). The claim-then-fulfil order retains the accepted crash window. |
| C-7 | Not mitigated (accepted). T5 includes a test that documents the behaviour (duplicate acknowledged, original fails, claim released) without asserting any automatic recovery. |
| C-8 | No provider adapter interface exists or is changed; verified by diff scope in T7. |

## Assumptions and unresolved questions

Assumptions (each is non-blocking and is recorded so the reviewer and approver can challenge it):

- **A1 (OQ-1, response values).** The repository has no HTTP layer; the "response" is the dict returned by `CallbackController.handle`. "Unchanged" is interpreted as: that dict for a first delivery and for a duplicate stays exactly `{"status": "ok", "payment_id": <event payment_id>}`, and a failing delivery keeps propagating an exception as today. No status code or failure-body is invented.
- **A2 (OQ-2, log content).** No format is specified. The plan uses INFO level, logger `payment_service.payment_service` (already pinned by the existing test), and a message `duplicate callback ignored provider_event_id=%s payment_id=%s`. Release of a claim after a failed fulfilment is logged separately (see design). Exact wording is an implementation choice within those bounds.
- **A3 (OQ-5, mandatory vs advisory).** AC-3 says "must", CONF:4812 says "should". The plan treats logging as mandatory (the stricter reading), which satisfies both.
- **A4 (OQ-3, scope beyond fulfilment).** The repository performs no payment state transitions; "payment processing" is only claim + fulfilment. The design gates exactly that.
- **A5 (OQ-4, "completed" and retention).** "Completed business processing" is taken to mean: the claim row exists and was not released. A claim row is retained indefinitely; no retention or purge is designed. In-flight and completed events are deliberately indistinguishable in storage, which is sufficient for FR-2 and FR-5 through the constraint alone, and avoids a schema change.
- **A6 (same event ID, different `payment_id`).** Not specified by any source. The first claim wins; the repository continues to ignore a later `payment_id`. A duplicate's response echoes the `payment_id` in its own body, matching the current response construction and the existing test.
- **A7 (release failure).** If `release_claim` itself raises while handling a fulfilment failure, the plan logs it, re-raises the original fulfilment exception, and leaves the claim stuck. This is treated as within the C-6 accepted limitation (an outcome not durably recorded); the sources do not explicitly cover it, so it is called out here and in Risks.
- **A8 (wiring change).** Satisfying C-4 requires changing the `PaymentService` constructor (repository instance replaced by a repository factory). `PaymentService` is an internal component, not a provider adapter, so C-8 is not affected. This is a material design choice under the governance policy (transaction/concurrency semantics) and is surfaced for the approver.
- **A9.** No developer guidance was supplied; no guidance decisions are applied.

Unresolved human decisions: none blocking. OQ-1 to OQ-5 remain open in the Planning Context as non-blocking and are handled by A1 to A5 above. If the approver wants a different failure response, log format or retention behaviour, the plan needs revision before implementation.

## Repository / impact analysis

- Affected components:
  - `app/payment_service/payment_service.py` (primary: claim branching, release on failure, logging, per-request repository).
  - `app/payment_service/repository.py` (new `release_claim`; docstring).
  - `app/tests/test_payment_service.py` (setUp wiring changed to the factory; existing assertions kept).
  - New test modules under `app/tests/` (repository, failure/log/response behaviour, concurrency).
  - Unchanged: `callback_controller.py`, `fulfilment_service.py`, `models.py`, `__init__.py`.
- Existing patterns to reuse:
  - Layering Controller → Service → Repository/Fulfilment with constructor injection, `from __future__ import annotations`, docstrings citing ticket and AC ids.
  - Repository style: raw `sqlite3`, one method per operation, commit inside the method, `IntegrityError` translated to a boolean.
  - Logging style: module logger with lazy `%s` formatting.
  - Tests: `unittest`, per-test temp directory with a file-backed SQLite database (required so that separate connections share one database; `:memory:` would give each connection its own database).
- Dependencies: Python standard library only (`sqlite3`, `logging`, `threading`, `unittest`, `tempfile`). No manifest or dependency change.
- Existing behaviour relied on: `sqlite3.connect` default `check_same_thread=True` (so a repository cannot be shared across request threads anyway) and the default 5-second busy timeout on file databases. These are general `sqlite3` module defaults, not repository facts, and are verified indirectly by the concurrency tests.

## Proposed design

### Order of operations (claim, then fulfil, outside the claim transaction)

```
process_callback(event):
    repo = self._repository_factory()          # fresh connection per request (C-4)
    try:
        if not repo.record_if_new(event.provider_event_id, event.payment_id):
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

- **Claim.** `record_if_new` is unchanged: an `INSERT` that commits immediately, returning `True` on success and `False` on `sqlite3.IntegrityError`. Because the claim commits before fulfilment starts, a concurrent duplicate sees the row (or waits briefly for the writer lock and then sees the row) and gets `IntegrityError`, never an application-level lock (C-1, FR-6). Concurrent inserts really race at the persistence layer regardless of timing (NFR-2).
- **Fulfilment is deliberately not run inside the claim transaction.** Holding a write transaction across `fulfil()` would make a concurrent duplicate on a file SQLite database block on the write lock up to the busy timeout and then raise `OperationalError` (not `IntegrityError`), which would break FR-5 and FR-7. Committing the claim first avoids this and matches the existing structure.
- **Non-duplicate claim failure (FR-4).** Any exception from `record_if_new` other than `IntegrityError` propagates out of the outer `try` (only `finally: close()` runs). No claim exists, so nothing is released and no bookkeeping is needed.
- **Release (FR-3, C-3).** A new `PaymentRepository.release_claim(provider_event_id)` executes `DELETE FROM processed_callbacks WHERE provider_event_id = ?` and commits. The retry then re-inserts through the same `record_if_new` `INSERT` under the same PRIMARY KEY, i.e. delete-then-reinsert under the constraint. There is no status column, no `UPDATE` and no compare-and-swap.
- **Release ownership.** `release_claim` is only reachable from the inner `except` that runs after this request's own claim returned `True`. A rejected duplicate returns before that point, so it can never delete a row it did not create. The release runs at most once per request and is not retried.
- **Release failure (A7).** `_release_after_failure` wraps `release_claim` in `try/except Exception`, logs the release failure at ERROR with the event ID, and the caller re-raises the original fulfilment exception (the release error is not allowed to replace it). A log entry is also emitted when a release succeeds (WARNING, event ID, "claim released after fulfilment failure"), which gives operations visibility into retried events. These extra log lines contain neither the word `duplicate` nor change any response.
- **Exception scope.** The inner handler catches `Exception` (not `BaseException`); process-level interrupts are treated like a crash and fall under C-6.

### Per-request repository (C-4)

- `PaymentService.__init__(self, repository_factory: Callable[[], PaymentRepository], fulfilment_service: FulfilmentService)` replaces the instance parameter. Each `process_callback` call calls the factory once, and closes the returned repository in `finally`.
- A factory keeps dependency injection consistent with the existing style, avoids hard-coding a database path in the service, and keeps `PaymentRepository`'s constructor `(db_path)` unchanged. In the tests the factory is `lambda: PaymentRepository(db_path)`. Because no composition root exists in the repository, production wiring is out of scope; any future composition root only needs to pass a factory that opens a fresh connection to the same store.
- `PaymentRepository.__init__` still runs `CREATE TABLE IF NOT EXISTS` and `commit()` on every construction. Under per-request construction this repeats on each request. It is a no-op once the table exists and no schema change is planned. Whether it causes lock contention under concurrency is an inference, not a verified fact, so T6 exercises it. If T6 shows contention, the contingency is to move schema creation out of per-request construction, and that contingency would be raised as a plan deviation rather than applied silently.
- `CallbackController` continues to receive one `PaymentService` instance; that instance holds no connection, so sharing it across concurrent requests is safe.

### Response and logging

- Duplicate path returns `ProcessingResult(payment_id=event.payment_id, fulfilled=False)`. The controller already ignores `fulfilled`, so the external response is byte-for-byte the same as for a first delivery (AC-2, FR-7).
- The duplicate INFO message uses lazy `%s` formatting like the existing line, on the existing module logger.

### Alternatives considered

- **Conditional `UPDATE ... WHERE status='failed'` for retry:** rejected; explicitly forbidden by C-3.
- **Fulfilment inside the claim transaction:** rejected (FR-5/FR-7 risk above).
- **Application lock or lock service:** rejected (C-1, FR-6).
- **Service opens `PaymentRepository(db_path)` itself:** rejected in favour of a factory (hard-coded path, harder to test failure injection).
- **Add a status column:** rejected (schema migration for no requirement; `CREATE TABLE IF NOT EXISTS` would not alter an existing table; C-3).

## Implementation tasks

| Task | Goal | Depends on | Parallelizable |
|---|---|---|---|
| T1 | `repository.py`: add `release_claim(provider_event_id)` (`DELETE` + `commit`, no return value); update the module docstring to describe claim/release under the uniqueness constraint and that this is not a lock. Do not change `record_if_new`, the schema or the constructor. Maps to FR-3, C-3. | - | Yes (with T4 test authoring once the signature is agreed) |
| T2 | `payment_service.py`: replace the repository parameter with `repository_factory`; per-request construct/close; branch on the `record_if_new` result (duplicate: INFO log with `duplicate`, return `fulfilled=False`); wrap `fulfil()` so a failure releases the claim (via `release_claim`), logs, and re-raises the original exception; remove the "Known gap" docstring and cite PAY-DEMO-001 behaviour instead. Maps to AC-1, AC-3, FR-2..FR-5, FR-7, C-4. | T1 | No |
| T3 | `app/tests/test_payment_service.py`: change `setUp`/`tearDown` to build the service with a `PaymentRepository` factory over the temp-dir database (no long-lived repository to close). Keep the four existing tests' assertions unchanged. | T2 | No |
| T4 | New `app/tests/test_repository.py`: `release_claim` deletes the row; `record_if_new` returns `True` again after release; releasing an unknown event ID is a no-op; multiple threads, each with its own `PaymentRepository` over one file database, racing on one event ID produce exactly one `True`. Maps to FR-3, FR-6, NFR-2, C-3. | T1 | Yes (with T2, T3) |
| T5 | New `app/tests/test_payment_idempotency.py` (service/controller level, single-threaded where possible): (a) fulfilment failure releases the claim and a retry with the same event ID fulfils exactly once; (b) the failure exception propagates unchanged out of `CallbackController.handle`; (c) a non-`IntegrityError` `sqlite3` error from `record_if_new` propagates, fulfilment not called, `release_claim` not called; (d) deterministic in-flight duplicate using a blocking fulfilment double (`threading.Event`, with timeouts so a defect fails rather than hangs): the duplicate returns the same response as a first delivery and does not fulfil; (e) C-7 documenting test: after (d) the original fails, the claim is released and no automatic re-delivery is asserted; (f) a release failure does not mask the original exception; (g) duplicate log entry contains `duplicate` and the event ID, and a first delivery emits no `duplicate` log; (h) one repository is constructed and closed per call (C-4). Maps to AC-1..AC-3, FR-3..FR-5, FR-7, C-4, C-7. | T2, T3 | Yes (with T6) |
| T6 | New `app/tests/test_callback_concurrency.py`: one shared `PaymentService` with a factory over a file database; N threads released together with `threading.Barrier`, all sending the same `provider_event_id`; assert fulfilment count is exactly 1, every response is identical, and all threads finished within a timeout. Repeat over several distinct event IDs per test run to increase the chance of real overlap. Also serves as the check on per-request DDL contention. Maps to AC-1, FR-5, FR-6, NFR-2, C-4. | T2, T3 | Yes (with T5) |
| T7 | Final verification: run the full suite and the deterministic checks listed below; confirm the diff touches only the files named in this plan; confirm docstrings are consistent with the new behaviour. | T1-T6 | No |

### Dependencies and parallelisation

- Critical path: T1 → T2 → T3 → (T5 ∥ T6) → T7.
- T4 needs only the T1 interface (`release_claim(provider_event_id) -> None`, fixed by this plan) and can proceed in parallel with T2 and T3.
- T5 and T6 are in separate files and are independent of each other once T2 and T3 are complete.
- T1 and T2 edit different files, but T2 calls the T1 method, so they are sequenced to avoid integration guesswork.
- Because the test files share no state, parallel test authoring has no merge conflict beyond the `setUp` factory pattern introduced in T3, which T5 and T6 should copy rather than import.

## Developer verification strategy

- **Build/static checks:**
  - `python3 -m compileall -q app` (syntax/import sanity; the project has no configured linter or type checker).
  - Grep guard for C-3: `grep -nE "UPDATE|status" app/payment_service/repository.py` must return nothing.
  - Grep guard for C-1/C-5: no new imports of locking, network or database packages in `app/payment_service/` (only `sqlite3`, `logging`, `pathlib`, `typing`, `collections.abc`/`__future__`).
- **Unit tests:** `python3 -m unittest discover -t app -s app/tests -v` (command from `app/README.md`). Expected: the four existing tests pass (two of them, `test_duplicate_callback_does_not_refulfil` and `test_duplicate_is_distinguishable_in_logs`, are expected to fail on the baseline and pass after T2, which serves as the red-to-green evidence for AC-1 and AC-3) plus the new tests in T4 and T5.
- **Integration/concurrency tests:** T6, and the threaded case in T4, against a real file-backed SQLite database with separate connections per request. Run the concurrency module several times (for example `for i in 1 2 3 4 5; do python3 -m unittest discover -t app -s app/tests -p 'test_callback_concurrency.py'; done`) to look for flakiness, since a race test can pass by chance. Assertions must depend on the outcome being exactly-one, not on a timing window.
- **Other deterministic checks:**
  - `git diff --stat` shows changes limited to `payment_service.py`, `repository.py`, `test_payment_service.py` and the new test modules; `callback_controller.py`, `models.py` and `fulfilment_service.py` are unchanged (AC-2, C-8).
  - No `.db` file or other artefact is left in the repository by the tests (temp directories are cleaned up in `tearDown`).
- **Not verifiable in this repository:** behaviour against a real PostgreSQL instance and a real provider (out of scope per C-5, C-8), and the exact production HTTP status codes (OQ-1). The plan makes no claim about those.

## Risks and compatibility / rollout considerations

- **Compatibility (API/contract):** the external response dict and `ProcessingResult` are unchanged in shape. `CallbackController` and `models.py` are untouched. The only signature change is the internal `PaymentService` constructor (A8); any code outside this repository that constructs `PaymentService` with a repository instance would break. No such caller exists in the repository (wiring exists only in tests). This is a material, approver-visible change.
- **Persistence/schema:** none. Same table, same PRIMARY KEY, same file. Existing databases and existing rows remain valid; rows written before the change are treated as "already processed", i.e. their duplicates will now be suppressed. No migration is needed.
- **Rollout / rollback:** no feature flag is needed; the change is confined to the callback path. Rollback is a code revert with no data cleanup, because claim rows written by the new code are of the same shape the old code writes. One behavioural note: during a rolling deployment where old and new instances share one database, an old instance still re-fulfils duplicates, so the guarantee holds only once all instances run the new code.
- **Security/authorization:** no change to authentication or authorization. Log lines carry `provider_event_id` and `payment_id`, which the existing log line already carries (event ID); `payment_id` is newly logged on the duplicate path, and no payment credentials or amounts are involved. Reviewer to confirm that `payment_id` in logs is acceptable.
- **Operational/observability:** new INFO duplicate line, WARNING on claim release after a fulfilment failure, ERROR if the release itself fails. Log level policy is undefined in the repository (A2).
- **Accepted limitations (not mitigated):**
  - C-6: crash after claim but before the outcome is durably recorded leaves the event permanently claimed with no automatic recovery.
  - C-7: a duplicate that arrived while the original was in flight receives a success acknowledgement; if the original then fails and is released, no further callback for that event ID will arrive and the payment is not recovered.
  - A7 (release failure) is treated as a variant of C-6 and stays stuck.
- **Technical risks:**
  - *Wrong owner releasing a claim:* mitigated by structure (release only after this request's own successful claim); covered by tests T5(a), T5(d), T5(e).
  - *Lock contention from per-request `CREATE TABLE IF NOT EXISTS`:* unverified inference; T6 exercises it; contingency described above would be a flagged deviation.
  - *Flaky or vacuous concurrency tests:* mitigated by the deterministic in-flight test (T5d), a repository-level exactly-one-winner test (T4) that does not depend on fulfilment timing, repeated runs, and timeouts on every wait.
  - *Same event ID with a different `payment_id`:* behaviour unspecified (A6); first claim wins.
  - *Existing-test edits:* T3 changes only the wiring in `setUp`/`tearDown`; the assertions of existing tests are not weakened.

## Independent plan review

Final independent review evidence is maintained in the sibling `plan-review.json` artifact and is cryptographically bound to this plan's SHA-256. The plan itself is not modified after the passing review merely to embed the review result.
