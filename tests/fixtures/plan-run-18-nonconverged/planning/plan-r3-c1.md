# Development Plan — PAY-DEMO-001

## Metadata

- Jira ticket: `PAY-DEMO-001` — "Make payment callbacks idempotent"
- Repository: `/home/dad/AIProjects/awscao_learning` (application under `app/payment_service/`)
- Base branch: `main`
- Repository baseline SHA: `a806a98e5a4b024ef5000d29a3458f7c219bc068`
- Planning Context: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-18/context/normalized/planning-context-v1.json` (schema 1.0, 0 contradictions, 0 retrieval warnings, 4 non-blocking open questions)
- Planning Analysis: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-18/analysis/planning-analysis-v1.md`
- Plan revision: r3 (revision of `plan-r2-c1.md` against `review-r2-c1.json`)

> This plan is immutable once presented for human approval. Its SHA-256 and approval state are stored separately. Independent agent-review evidence is stored in `plan-review.json` and is bound to the SHA-256 of the exact plan content reviewed.

### Revision notes (r3)

The validated requirements are unchanged. Prior design decisions are preserved: PRIMARY KEY as the sole arbiter, release by plain `DELETE`, repository factory for per-request repositories, controller untouched, release-and-re-raise for any `BaseException` raised by `fulfil`, T0 as strict predecessor, AST-based T8 check. Finding IDs below are those of `review-r2-c1.json` (they restart per review round and are not the r1 IDs cited in earlier plan revisions).

| Finding | Disposition | Change made in r3 |
|---|---|---|
| PLAN-003 (`close()` failure unspecified; could mask the original exception or fail a completed fulfilment) — PLAN_CHANGE_REQUIRED | Addressed | Design: `close()` is now called through `_close_quietly`, which catches `Exception`, logs a WARNING with traceback and **never raises from the `finally`** (new assumption A10; design; T2). New test T5(h) covers close failure on the success, duplicate and failed-fulfilment paths. T5(f) extended to cover close being attempted on every path. |
| PLAN-004 (T5(d) `subTest` ambiguous; could contradict the design on `BaseException` during release) — PLAN_CHANGE_REQUIRED | Addressed | T5(d) rewritten: the `subTest` varies **only the exception raised by `fulfil`** (`Exception` subclass / `BaseException` subclass); the injected release failure is **always an `Exception` subclass**. A `BaseException` raised *during release* is explicitly out of the test set; the design states its expected propagation (A4, design) and why it is not asserted. |
| PLAN-001 (INFO default risks AC-3 under Python's default WARNING level) — ADVISORY | Addressed | The proposed default for the duplicate log is now **WARNING** (A6, AC-3 row, design, T2, T5(e)); HD-4 is presented as an explicit decision with the default recorded and INFO as the named alternative. |
| PLAN-002 (HD-1/HD-2 must stay visible as approval items) — ADVISORY | Retained | HD-1 and HD-2 remain in "Decisions requiring explicit human approval", and are repeated in the Risks section. No change to their content. |
| PLAN-005 (T6(c) encodes CON-5 limitation as a passing test) — ADVISORY | Addressed | T6(c) is labelled a **characterization test of an accepted limitation**; it asserts only the acknowledgement and the release, and no longer asserts the absence of recovery. |
| PLAN-006 (dependency on close-in-finally to release the SQLite write lock after a rejected duplicate; race-test bounds) — ADVISORY | Addressed | Design "Concurrency under SQLite" now notes that `record_if_new` does not roll back after `IntegrityError` and that `close()` in `finally` is what ends the implicit transaction; T4 gains a test for this; T6(b) asserts every thread completes within a bounded join. R4 updated. |

## Source references

### Jira
- `JIRA:PAY-DEMO-001` — Make payment callbacks idempotent (Description, Scope, Acceptance Criteria AC-1..AC-3). Digest `63d3b4f5…39991`.

### Confluence
- `CONF:4812` — Payment Callback Processing (functional requirements, observability, compatibility, background on provider event ID stability). Digest `e2524c60…a43`.
- `CONF:4934` — Payment Service Architecture (constraints CON-1..CON-5, persistence, transaction model, relevant components). Digest `01527c41…9064`.

## Problem statement

Payment providers can send the same callback more than once. On the baseline, `PaymentService.process_callback` (`app/payment_service/payment_service.py:23-27`) calls `PaymentRepository.record_if_new(...)` and **discards its boolean result**, then always fulfils, logs `"callback processed …"` and returns `fulfilled=True`. A repeated provider event therefore triggers fulfilment again, and duplicates cannot be told apart from first deliveries in the logs.

The change must make callback handling idempotent on the provider event ID, using the persistence-layer uniqueness constraint (already present as `PRIMARY KEY` on `processed_callbacks.provider_event_id`), while keeping the external response contract unchanged, releasing a claim when fulfilment fails so a provider retry can succeed, and logging duplicates distinguishably.

## Scope

### In scope
- The payment callback processing path: `PaymentService.process_callback`, the claim/release operations on `PaymentRepository`, and the way `PaymentService` obtains and disposes of a `PaymentRepository` per request (NFR-2).
- Continuing to use `PaymentRepository`'s existing SQLite-backed implementation and existing table schema as the stand-in for PostgreSQL (CON-6).
- Tests covering AC-1..AC-3, FR-1..FR-7, NFR-1..NFR-3 (test-only failure/gating stubs live in the test modules).
- Docstring updates that record the new behaviour and remove the stale "Known gap" note.

### Out of scope
- Provider adapter public interfaces (CON-7). None exist in `app/`; none will be added or changed.
- Introducing an actual PostgreSQL dependency (CON-6).
- Any distributed lock, application-level lock or lock service (CON-1, NFR-1).
- Any status column or status-based compare-and-swap (CON-3).
- Automatic reconciliation/timeout recovery for a crash between acceptance and durable outcome (CON-4).
- Automatic recovery of a payment whose in-flight original fails after a duplicate was already acknowledged with success (CON-5).
- Any change to the response body or to exception behaviour of `CallbackController.handle` (AC-2, FR-7).
- Schema changes/migrations, an HTTP layer, a composition root/entry point, and adding logging configuration (handlers/levels) to the application.

## Acceptance criteria

| ID | Criterion | Planned implementation | Planned verification |
|---|---|---|---|
| AC-1 | Repeated callbacks with the same provider event ID must not cause duplicate fulfilment. | T2: `process_callback` acts on the `record_if_new` result; on `False` it skips fulfilment. Claim is committed before fulfilment, so completed and in-flight originals are both detected by the same PRIMARY KEY. Covers FR-1, FR-4, NFR-1. | T3/T6: sequential duplicate test (existing `test_duplicate_callback_does_not_refulfil`, retained); in-flight duplicate test with a gated fulfilment stub; N-thread race test asserting exactly one fulfilment and completion within a bounded join. |
| AC-2 | Existing provider-facing HTTP response behaviour must remain backward compatible. | `CallbackController.handle` is **not modified**; it still returns `{"status": "ok", "payment_id": <incoming payment_id>}` for first delivery and duplicate alike (FR-5, FR-7). A failed fulfilment continues to propagate the exception out of `handle`, exactly as on baseline; a failing `close()` never alters the response or the propagated exception (A10). | Existing `test_first_callback_fulfils_once` and `test_response_shape_is_identical_for_new_and_duplicate` retained; new assertions that the in-flight duplicate's response equals the first delivery's; new tests that a fulfilment `Exception` and a `BaseException` still propagate through `handle`; T5(h) close-failure tests confirm responses/exceptions are unchanged; `git diff` check that `callback_controller.py` is untouched (T8). |
| AC-3 | Duplicate callback attempts must be observable in application logs. | T2: duplicate path logs on `payment_service.payment_service` at **WARNING** a message beginning `duplicate callback ignored` with `provider_event_id` and `payment_id`. The first-delivery message `callback processed …` (INFO) is unchanged and never contains "duplicate". Failure, release, release-failure and close-failure outcomes are also logged (WARNING/ERROR). Covers FR-6, NFR-3. WARNING is chosen because Python's default effective level is WARNING and the repository configures no logging (see A6/HD-4). | Existing `test_duplicate_is_distinguishable_in_logs` retained (`assertLogs(level="INFO")` also captures WARNING); new test that first-delivery output does not contain "duplicate" and that the duplicate record's level is WARNING; log assertions for release, release-failure and close-failure paths. |

Requirement coverage beyond the AC list:

| Requirement | Task(s) | Verification |
|---|---|---|
| FR-1 completed event is not re-fulfilled | T2 | sequential duplicate test |
| FR-2 release claim on fulfilment failure (any `Exception` or `BaseException` raised by `fulfil`); retry can re-claim | T1, T2 | repository release test; service tests: failing fulfilment (both exception kinds) → row deleted → same event ID retried → fulfilled once |
| FR-3 claim failure (non-duplicate) needs no release bookkeeping | T2 (claim call sits outside the release-on-failure `try`; only `IntegrityError` is treated as duplicate, already true in `record_if_new`) | test with a repository whose `record_if_new` raises `sqlite3.OperationalError`: no fulfilment, no `release_claim` call, error propagates, a repeated attempt then succeeds |
| FR-4 duplicate during in-flight processing must not fulfil | T2 | gated in-flight test |
| FR-5 in-flight duplicate gets the same successful response | T2 (controller unchanged) | in-flight test compares responses |
| FR-6 / NFR-3 logs distinguish duplicate from first | T2 | log tests |
| FR-7 response contract unchanged | none (controller untouched) | as AC-2 |
| NFR-1 uniqueness constraint, no lock | T1, T2 | race test; AST-based check that production code adds no `threading`/lock usage and no `UPDATE` SQL (T8); schema-shape test (T4) |
| NFR-2 per-request repository, fresh connection to the same store | T2, T3 | test asserting the factory is called once per callback and each repository is closed on every path (including when `close()` fails); race test uses a file-backed DB with one repository per thread |
| CON-3 all idempotency writes via the uniqueness constraint (retry = delete then re-insert) | T1, T2 | release is a plain `DELETE`; retry re-enters through `record_if_new`; schema-shape test (no status column) and AST check for `UPDATE` SQL (T4, T8) |
| CON-4, CON-5 accepted limitations | none (no recovery code) | documented in docstring and Risks; CON-5 has a characterization test (T6(c)) that asserts only the acknowledgement and the release, not the absence of recovery |
| CON-6 SQLite kept, no PostgreSQL | all | no new dependency; schema unchanged |
| CON-7 provider adapter interfaces untouched | all | no provider adapter exists in `app/`; diff confined to files listed below |

## Assumptions and unresolved questions

Assumptions (each is a design choice within stated requirements, not a change to them):

- **A1 — OQ-3 resolved by repository evidence.** The uniqueness constraint already exists (`repository.py:20-27`, `provider_event_id TEXT PRIMARY KEY`). No schema change and no migration are planned.
- **A2 — CON-6 "as-is" refers to the engine and schema.** Adding a `release_claim` method is required by FR-2 and does not alter the schema. (See HD-2.)
- **A3 — Claim semantics (OQ-4).** A row in `processed_callbacks` means "claimed"; it is committed before fulfilment starts. A surviving row means completed *or* in flight; no status is stored (CON-3 forbids a status-based compare-and-swap, and a status column is not required by any acceptance criterion). Duplicate handling, response and log are the same for completed and in-flight originals. (See HD-5.)
- **A4 — Failure = anything raised out of `fulfil` while the process is alive.** FR-2 requires release when fulfilment "raises or otherwise fails". The plan therefore releases the claim and re-raises for **any `BaseException`** raised by `fulfil(...)` (including `Exception` subclasses, `SystemExit`, `KeyboardInterrupt`, cancellation-style exceptions). In all of those cases the interpreter is still running and can execute the release, so treating them as CON-4 would widen the accepted limitation beyond what CON-4 authorises. The original exception is always the one re-raised. The CON-4 crash case is limited to the process (or host) ceasing to execute before the outcome is durably recorded (e.g. SIGKILL, OOM kill, power loss), plus a second asynchronous interrupt arriving *during* the release itself or in the instant between the claim commit and entry into the guarded block. For that second-interrupt case the expected behaviour is stated, not tested: a `BaseException` raised during release is **not caught** by `_release_after_failure` (which handles `Exception` only), so it propagates in place of the original exception and the claim may remain; no in-process recovery is provided and no test asserts this behaviour, because triggering it deterministically would require injecting an interrupt into the release call and would test interpreter semantics rather than this change.
- **A5 — Failed-fulfilment response (OQ-1).** The baseline has no defined failure response: the exception propagates out of `CallbackController.handle`. The plan preserves that (re-raise the original exception after releasing the claim), so the externally visible behaviour is unchanged. No new response shape is invented. (See HD-3.)
- **A6 — Log level/format (OQ-2).** Duplicate: **WARNING**, `duplicate callback ignored for provider_event_id=%s payment_id=%s`. The only hard constraint in the repository is the existing test (INFO or higher on logger `payment_service.payment_service`, output containing "duplicate", case-insensitive); WARNING satisfies it identically. The repository contains no logging configuration (no `basicConfig`/`dictConfig`) and Python's default effective level is WARNING, so an INFO duplicate line could be invisible in the only runtime configuration that can be assumed, which would put AC-3 at risk. WARNING is therefore the proposed default; the first-delivery `callback processed …` line stays at INFO (unchanged). The cost is that expected provider retransmissions appear at WARNING; distinguishing them from failure WARNINGs relies on the distinct message prefixes. Observability is demonstrated in this plan only by `assertLogs` tests; the deployment's logging configuration is neither examined nor changed. (See HD-4.)
- **A7 — Duplicate `payment_id` mismatch.** The response continues to echo the incoming event's `payment_id` (unchanged behaviour). A duplicate carrying a different `payment_id` under the same event ID is ignored like any other duplicate; no comparison with the stored value is added because no source requires it.
- **A8 — Provider event ID stability** across retransmissions (CONF:4812) cannot be verified from the repository and is taken as given.
- **A9 — Python 3.10, stdlib only.** No new dependencies.
- **A10 — `close()` failure never changes the outcome of a request.** The repository is disposed of in a `finally`. A failure of `close()` (an `Exception`) is caught by a helper `_close_quietly`, logged at WARNING with traceback, and **never raised from the `finally`**. Consequently a close error cannot (i) replace the original fulfilment exception that is being propagated, (ii) turn an already-completed fulfilment into a raised error (which would make the provider retry and receive a duplicate acknowledgement for an event that was in fact fulfilled), or (iii) change the duplicate response. Release is always attempted *before* close, so a close failure cannot prevent the release from committing. A `BaseException` raised by `close()` is not caught (same asynchronous-interrupt reasoning as A4). Because `close()` is what ends the implicit SQLite transaction left open after a rejected duplicate (see "Concurrency under SQLite"), a failed close may keep the write lock until the connection is garbage-collected; this is logged and is bounded by the per-request connection lifetime.

### Decisions requiring explicit human approval at the plan gate

Governance classes component boundaries, persistence behaviour and transaction/concurrency semantics as material. HD-1 and HD-2 are design decisions whose approval belongs to the human; **a different answer to either sends the plan back for revision** (they are not to be resolved by the implementer).

- **HD-1 — `PaymentService` constructor signature (affects T2, T3). Approval required.** NFR-2 requires a fresh `PaymentRepository` per request, but `PaymentService.__init__(repository, fulfilment_service)` currently holds one long-lived instance, and the `sqlite3` default `check_same_thread=True` makes sharing across threads fail. The plan changes the first parameter to a zero-argument **repository factory** (`Callable[[], PaymentRepository]`). `PaymentService` and `CallbackController` are internal classes, not provider adapter interfaces (CON-7), but that is an inference; out-of-tree consumers of the old signature cannot be ruled out from this repository. Alternative if rejected: `PaymentService(db_path, fulfilment_service)` constructing the repository internally (less testable for failure injection). Either answer changes T2/T3.
- **HD-2 — CON-6 "as-is". Approval required.** Confirm that adding one method (`release_claim`) to the existing SQLite `PaymentRepository` is compatible with "keep using as-is". If not, FR-2 cannot be met without a different mechanism and the plan must be revisited.
- **HD-4 — OQ-2 log level and message format (affects T2, T5(e)). Decision requested at the gate.** Default proposed by this plan: duplicate line at **WARNING** with the message shape in A6, chosen so that AC-3 holds under Python's default WARNING-level logging. Alternative: INFO (if duplicates are considered normal traffic and the deployment's logging configuration is confirmed to enable INFO for `payment_service.payment_service`), or any other level/field set the human specifies. The choice is confined to one log call and the level assertion in T5(e). The human's choice should be recorded with provenance.

### Other unresolved human decisions (non-blocking; the plan proceeds on the stated default)

A differing answer would need re-planning of the noted task.

- **HD-3 — OQ-1 failed-fulfilment response.** Confirm that "response contract unchanged" includes propagating the fulfilment exception (baseline behaviour) rather than returning some error body.
- **HD-5 — OQ-4 completed vs in-flight.** Confirm that no distinction (in storage, logs, or response) between an already-completed and an in-flight original is required.
- **HD-6 — `BaseException` handling (A4).** Confirm that releasing the claim for `BaseException` (not only `Exception`) is the intended reading of FR-2. If the human instead wants `BaseException` treated as the CON-4 case, the change is confined to the `except` clause in T2 and to test T5(g).

No blocking questions or contradictions exist in the Planning Context.

## Repository / impact analysis

- Affected components:
  - `app/payment_service/payment_service.py` — behavioural change (claim result handling, release on failure, logging, per-request repository with non-raising disposal).
  - `app/payment_service/repository.py` — new `release_claim` method; docstring update. Table schema and `record_if_new` unchanged.
  - `app/tests/test_payment_service.py` — fixture rewired to a repository factory; new service-level and concurrency tests.
  - `app/tests/test_repository.py` — **new** unit tests for `record_if_new` / `release_claim` and schema shape.
  - Not changed: `callback_controller.py` (contract unchanged), `models.py` (existing `ProcessingResult.fulfilled` is reused: `True` for a fulfilled first delivery, `False` for a duplicate; the controller ignores it), `fulfilment_service.py` (test stubs subclass it inside the tests), `app/README.md` (test command unchanged), `__init__.py`.
- Existing patterns to reuse:
  - `unittest` with a `tempfile.TemporaryDirectory` file-backed SQLite database (`test_payment_service.py:13-23`); run with `python3 -m unittest discover -t app -s app/tests -v`.
  - `assertLogs("payment_service.payment_service", level="INFO")` for log assertions.
  - `logging.getLogger(__name__)` module logger with `%s` lazy formatting.
  - The existing PRIMARY KEY + `sqlite3.IntegrityError` → `False` idiom in `record_if_new` (CON-2).
  - `FulfilmentService.ledger` / `fulfilment_count` for asserting fulfilment counts.
- Dependencies:
  - Runtime: Python 3.10 stdlib (`sqlite3`, `logging`, `threading` in tests only).
  - Call chain: `CallbackController.handle` → `PaymentService.process_callback` → `PaymentRepository.record_if_new` / `release_claim` / `close` and `FulfilmentService.fulfil`.
  - Storage: file-backed SQLite; the PRIMARY KEY must remain. `:memory:` paths give each connection a separate database and cannot be used for multi-connection tests.
  - Logging: no logging configuration exists in `app/`; the code only emits records on a module logger.
- Baseline test state (inferred by reading, not executed): the AC-1 and AC-3 tests should fail on baseline; the first-callback and response-shape tests should pass. T0 confirms this and is the only baseline evidence.

## Proposed design

### Mechanism (CON-1, CON-3, NFR-1)

The provider event ID's `PRIMARY KEY` is the only arbiter. Every idempotency-relevant write goes through it:

1. **Claim** — `record_if_new(event_id, payment_id)`: `INSERT`, commit, return `True`; `IntegrityError` → `False`. Unchanged. The commit happens before fulfilment, so a concurrent duplicate on another connection sees the claim immediately.
2. **Release** — new `PaymentRepository.release_claim(provider_event_id) -> None`: `DELETE FROM processed_callbacks WHERE provider_event_id = ?`, then commit. This is a plain delete, not a status-conditional update.
3. **Retry after failure** — a later delivery of the same event ID runs the ordinary claim `INSERT` against the now-empty key. That is delete-then-reinsert under the same constraint, exactly as CON-3 describes.

No status column, no `UPDATE`, no lock of any kind is added.

### `PaymentService.process_callback`

```python
def __init__(self, repository_factory: Callable[[], PaymentRepository],
             fulfilment_service: FulfilmentService):
    self._repository_factory = repository_factory
    self._fulfilment_service = fulfilment_service

def process_callback(self, event):
    repository = self._repository_factory()          # fresh connection per request (NFR-2)
    try:
        if not repository.record_if_new(event.provider_event_id, event.payment_id):
            logger.warning("duplicate callback ignored for provider_event_id=%s payment_id=%s", ...)
            return ProcessingResult(payment_id=event.payment_id, fulfilled=False)
        try:
            self._fulfilment_service.fulfil(event.payment_id)
        except BaseException:                         # FR-2: any failure while the process is alive
            self._release_after_failure(repository, event)   # never masks the original error
            raise                                     # unconditional re-raise of the original
        logger.info("callback processed for provider_event_id=%s", event.provider_event_id)
        return ProcessingResult(payment_id=event.payment_id, fulfilled=True)
    finally:
        self._close_quietly(repository)               # never raises an Exception (A10)
```

`_release_after_failure` calls `repository.release_claim(...)`:
- on success: `logger.warning("fulfilment failed; claim released for provider_event_id=%s payment_id=%s", ...)` (with traceback via `exc_info=True`);
- if the release itself raises an `Exception`: `logger.exception("fulfilment failed and claim release failed; provider_event_id=%s remains claimed and unresolved", ...)`, and the **original exception** is still the one re-raised (the release error is logged, not raised). A `BaseException` arriving during the release is not caught and propagates in place of the original; that is the residual CON-4 case described in A4.

`_close_quietly(repository)`:
```python
try:
    repository.close()
except Exception:
    logger.warning("failed to close repository after callback processing", exc_info=True)
```
It sits in the `finally`, so it runs on every path (duplicate, success, failed fulfilment, failed release) and, because it swallows `Exception`, cannot change the value being returned or the exception being propagated (A10).

Behavioural properties:
- The `record_if_new` call sits outside the release `try`. A non-duplicate claim failure (`sqlite3.OperationalError`, e.g. "database is locked") propagates with no fulfilment and no release (FR-3); the provider's retry repeats the claim. The repository is still closed (quietly) in the `finally`. If the factory itself raises, no repository exists and the error propagates unchanged.
- The success log line sits after the guarded `fulfil` call, so a failure of the logging call after a completed fulfilment cannot release the claim of an event that was in fact fulfilled.
- A duplicate arriving while the original is in flight gets `record_if_new → False`, is logged as a duplicate, and returns the same value as any duplicate; the controller turns it into the same `{"status": "ok", ...}` body (FR-4, FR-5). If the original then fails and releases, no further callback will arrive for that event, which is the accepted CON-5 limitation; the release WARNING log is the only trace.
- Only the delivery that won the claim can reach the release code (duplicates return earlier), so a release cannot delete another delivery's claim (Analysis R3). After a release, a later re-claimant is the sole owner of the new row.
- Release is always attempted and committed before `close()` runs, so a `close()` failure cannot prevent a release.

### `CallbackController`

Unchanged. It keeps building `{"status": "ok", "payment_id": result.payment_id}` and does not read `fulfilled`.

### Concurrency under SQLite

Each request has its own connection to the same file. SQLite serialises writers; a second writer waits (default 5 s busy timeout) for the first commit and then receives `IntegrityError` → duplicate. If the wait times out, `OperationalError` propagates as a non-duplicate claim failure (FR-3). Connection settings are left as-is (CON-6). Behaviour under PostgreSQL would differ in locking details but uses the same unique-violation mechanism.

**Dependency on close-in-finally (inferred from `repository.py:30-40` and sqlite3's default transaction handling; not executed).** `record_if_new` does not roll back after `IntegrityError`, so the implicit transaction opened by the failed `INSERT`, and the write lock it holds, appear to remain open until the connection is closed. The per-request lifecycle (`close()` in `finally`, on every path) is therefore what releases the lock on the duplicate path; it is load-bearing, not just tidy. This is safe with the planned lifecycle, and it explains why race-test losers can serialise behind one another's short-lived transactions. `record_if_new` is deliberately left unchanged (adding a rollback would be a persistence-behaviour change outside the plan); T4 adds a test that pins the dependency, and T6(b) asserts that all threads complete within a bounded join. If `close()` fails, A10 applies: it is logged, not raised.

### Explicitly rejected alternatives

- `UPDATE … SET status='failed' … WHERE status=…` retry path or a status column: violates CON-3.
- A `threading.Lock`/lock service around the claim: violates CON-1/NFR-1.
- Sharing one `PaymentRepository` across requests: violates NFR-2 and raises `ProgrammingError` across threads.
- Releasing only on `except Exception`: leaves the claim stuck for `BaseException` in a live process and widens CON-4 beyond its stated scope.
- A bare `finally: repository.close()`: an exception from `close()` would replace the original fulfilment exception or fail an already-completed fulfilment; replaced by `_close_quietly` (A10).
- Adding a duplicate flag to `ProcessingResult`, or changing the controller: unnecessary; increases contract risk.
- Adding an explicit `rollback()` to `record_if_new`: outside the plan's persistence-behaviour footprint; the close-in-finally lifecycle already ends the transaction.

## Implementation tasks

| Task | Goal | Depends on | Parallelizable |
|---|---|---|---|
| T0 | Baseline check, **strict predecessor of every edit**. In the implementation working tree before any file is modified: confirm `git rev-parse HEAD` equals `a806a98e5a4b024ef5000d29a3458f7c219bc068` and `git status --porcelain -- app` is empty; run `python3 -m unittest discover -t app -s app/tests -v`; record which tests pass/fail (expected: AC-1 and AC-3 tests fail, first-callback and response-shape tests pass). Confirms the Analysis's inferred baseline. If the baseline differs from expectation, record it and escalate before proceeding. No files changed. (Equivalent alternative: run against a separate `git worktree` of the baseline SHA, in which case T1 may start once the worktree exists; the default is sequential.) | - | No (must complete before T1 and T2) |
| T1 | `repository.py`: add `release_claim(provider_event_id)` (`DELETE` + commit); update module docstring to describe claim/release under the constraint and the fact that a rejected duplicate leaves the transaction open until `close()`; leave schema and `record_if_new` untouched. Maps to FR-2, CON-3. | T0 | No (edits the tree T0 reads; see parallelisation) |
| T2 | `payment_service.py`: switch constructor to a repository factory; per-request repository disposed via `_close_quietly` in `finally` (catches and WARNING-logs `Exception` from `close()`, never raises, A10); act on the `record_if_new` result; duplicate → **WARNING** log + `fulfilled=False`; failure raised out of `fulfil` (`except BaseException`) → `_release_after_failure` (release, log, catches `Exception` from release only) then re-raise original; keep the first-delivery INFO log line outside the guarded block; replace the "Known gap" docstring with the new behaviour and the CON-4/CON-5 accepted limitations. Maps to AC-1, AC-3, FR-1..FR-6, NFR-1..NFR-3. | T1 | No |
| T3 | `test_payment_service.py` fixture: build a `db_path`, construct one repository once to create the table, and wire `PaymentService(lambda: PaymentRepository(db_path), fulfilment_service)`; drop the shared `self.repository` and its `tearDown` close. Existing four tests keep their assertions. Maps to NFR-2. | T2 (signature) | No |
| T4 | New `app/tests/test_repository.py`: `record_if_new` returns `True` then `False`; `release_claim` deletes the row so the same ID can be claimed again; releasing an unknown ID is a no-op; a claim from a second connection to the same file is rejected as duplicate; **transaction-lifetime test** (PLAN-006): after connection B's `record_if_new` returns `False` for a duplicate, connection B is closed and connection C can then claim a different ID without `OperationalError` (pins the dependency on `close()` ending the implicit transaction; if the observed SQLite behaviour turns out not to hold a lock, the test still passes and the note in the design is corrected during implementation); **schema-shape test**: `PRAGMA table_info(processed_callbacks)` returns exactly `provider_event_id` (primary key) and `payment_id`, i.e. no status column was added (CON-3). Run this module on its own (`python3 -m unittest discover -t app -s app/tests -p test_repository.py -v`) while T2 is in progress, since it imports only `repository.py`. Maps to FR-2, NFR-1, CON-3. | T1 | Yes (with T2) |
| T5 | `test_payment_service.py` service-level tests (test-only `FulfilmentService` and `PaymentRepository` subclasses defined in the test module; repository subclasses that override `close`/`release_claim` still delegate to the real implementation so connections are really closed and the temp directory can be removed): **(a)** failing fulfilment (`Exception`) → exception propagates through `controller.handle`, ledger empty, claim row gone, WARNING log; **(b)** retry of the same event ID after that failure succeeds and fulfils exactly once (FR-2); **(c)** non-duplicate claim failure (repository stub raising `sqlite3.OperationalError` from `record_if_new`) → no fulfilment, no release, error propagates, next attempt succeeds (FR-3); **(d)** release failure → the **original** exception is still raised and an ERROR log is emitted (R2). The `subTest` varies **only the exception raised by `fulfil`** (one `Exception` subclass, one private `BaseException` subclass); the injected failure of `release_claim` is **always an `Exception` subclass** (`sqlite3.OperationalError`). A `BaseException` raised *during* release is deliberately **not** tested (A4: expected to propagate in place of the original; not asserted); **(e)** first-delivery log lacks "duplicate"; duplicate log contains event and payment IDs and its record level is WARNING (AC-3, HD-4); **(f)** factory called once per callback and every repository's `close` is attempted, including on the duplicate, failed-fulfilment and claim-failure paths (NFR-2); **(g)** **`BaseException` from fulfilment** — a test-only `class _FulfilmentAborted(BaseException)` raised by the stub → propagates unchanged through `controller.handle`, claim row gone, WARNING log, repository closed, a retry with the same event ID then fulfils exactly once (FR-2, A4/HD-6). The test uses a private `BaseException` subclass rather than `KeyboardInterrupt`/`SystemExit` so it cannot interfere with the test runner; **(h)** **`close()` failure** (PLAN-003, A10) — a repository subclass whose `close()` closes the real connection and then raises `sqlite3.ProgrammingError`, run under `subTest` for three paths: (i) *first delivery succeeds* → `handle` returns the normal `{"status": "ok", ...}` response, ledger count is 1, the claim row remains, a WARNING "failed to close repository" is logged, no exception escapes; (ii) *duplicate* → response equals the first delivery's, no fulfilment, duplicate WARNING and close-failure WARNING both logged; (iii) *fulfilment raises and `close()` also raises* → the **original fulfilment exception** (not the close error) propagates, the claim row is gone (release committed before close), close-failure WARNING logged. | T3 | Yes (with T6) |
| T6 | `test_payment_service.py` concurrency tests using `threading` and per-request repositories on one file-backed DB: **(a)** **in-flight duplicate** — the original's fulfilment blocks on a `threading.Event`; the duplicate is submitted while it is blocked, returns the same response as a first delivery and fulfils nothing; releasing the event completes the original with count 1 (FR-4, FR-5); **(b)** **race** — N (about 8) threads released together by a `threading.Barrier` submit the same event ID; exactly one fulfilment, all N responses equal; every thread must finish within a bounded `join` timeout and the test fails if any thread is still alive, which also guards against lock-serialisation stalls (AC-1, NFR-1, NFR-2, PLAN-006); **(c)** **characterization test of an accepted limitation (CON-5)** — the in-flight original fails and releases after a duplicate was already acknowledged: assert only that the duplicate was acknowledged with the normal response and that the claim was released (row gone). It does **not** assert that no automatic recovery/re-fulfilment occurs, so a future reconciliation ticket can add recovery without inverting this test; its docstring says so. Use events/barriers, not sleeps, to order steps. | T3 | Yes (with T5) |
| T7 | Docstring/comment consistency check across `payment_service.py`, `repository.py` (and confirm `callback_controller.py` docstring remains accurate without edits). Covered inside T1/T2 edits; this task is only a final read-through. | T2 | Yes |
| T8 | Final deterministic verification: (1) full unittest run; (2) repeat the concurrency tests several times to check for flakiness; (3) `git diff --stat` shows changes confined to `payment_service.py`, `repository.py`, `test_payment_service.py`, `test_repository.py`; (4) `git diff -- app/payment_service/callback_controller.py app/payment_service/models.py app/payment_service/fulfilment_service.py pyproject.toml` is empty; (5) **structural CON-1/CON-3 check** — a throwaway script (run from the scratchpad, not committed) parses each module under `app/payment_service/` with `ast`, ignores docstrings and comments, and fails if it finds an import of `threading`, a reference to `Lock`/`RLock`, or a string constant in executable code matching `\bUPDATE\b` (case-insensitive). This avoids the false positives a plain-text grep would give on docstrings describing CON-3 and on the controller's `"status": "ok"`. The `status`-column concern is covered by the T4 schema-shape test instead. The check is a proxy; the reviewer/human read-through of the diff remains the authoritative check for CON-1 and CON-3. | T3, T4, T5, T6, T7 | No |

### Dependencies and parallelisation

- Critical path: T0 → T1 → T2 → T3 → (T5 ∥ T6) → T8.
- **T0 is a strict predecessor of T1 and T2.** It must finish on the untouched baseline before any working-tree edit begins, so the baseline evidence cannot observe a half-edited `repository.py` or a changed constructor. T0 is not parallelisable with T1.
- Safe parallel work:
  - T4 with T2: T4 needs only T1, edits a different file, and is run as its single module so it does not import `payment_service.py` while T2 is mid-edit.
  - T5 with T6: different test classes in the same module; if one developer, write sequentially to avoid edit conflicts in the same file, or place T6 in a separate class/section.
  - T7 can overlap with T3–T6 once T2 has landed.
- T2 and T3 must not run in parallel: the fixture depends on the new constructor signature.
- T1 and T2 edit different files and could be drafted concurrently after T0, but T2 cannot be verified until T1 lands.

## Developer verification strategy

- Build/static checks:
  - `python3 -m compileall app` (syntax) — no linter/type-checker is configured in the repo (`pyproject.toml` has no tooling config per the analysis), so none is added.
  - The T8 diff checks (controller/models/fulfilment/`pyproject.toml` untouched, changes confined to the four listed files) and the T8 AST-based check that executable code adds no `threading`, `Lock`/`RLock` or `UPDATE` SQL. The reviewer read-through of the diff remains authoritative for CON-1/CON-3.
- Unit tests (`python3 -m unittest discover -t app -s app/tests -v`):
  - Repository: claim, duplicate, release, re-claim, unknown-ID release, cross-connection duplicate, transaction lifetime after a rejected duplicate, schema shape with no status column (T4).
  - Service/controller: sequential duplicate, response equality, log distinguishability and level, release-on-failure for `Exception` and `BaseException`, retry after release, claim-failure path, release-failure path (fulfilment exception kind varied, release failure always an `Exception`), per-request repository lifecycle, `close()` failure on the success, duplicate and failed-fulfilment paths (T3, T5).
- Integration tests (in-process, real file-backed SQLite, no mocks for persistence):
  - In-flight duplicate with a gated fulfilment stub, the N-thread race with bounded joins, and the CON-5 characterization test (T6). These exercise the real uniqueness constraint across separate connections, which is the substance of NFR-1/NFR-2.
- Other deterministic checks:
  - T0 baseline run on the unmodified baseline checkout to confirm the expected fail→pass transition of the AC-1 and AC-3 tests.
  - Re-run the concurrency tests repeatedly (for example 20 iterations in a shell loop) to detect flakiness before hand-off; tests must not rely on sleeps.
- Not verifiable here: real PostgreSQL behaviour; real provider retransmission behaviour (A8); whether the deployment's logging configuration surfaces the chosen level (A6/HD-4) — `assertLogs` proves the record is emitted at the chosen level, not that an operator sees it; behaviour when a `BaseException` interrupts the release or close call itself (A4/A10).

## Risks and compatibility / rollout considerations

- **API/response compatibility (AC-2, FR-7):** `CallbackController` is untouched and the response body is identical for first, duplicate and in-flight-duplicate deliveries. Exception propagation on failed fulfilment (including `BaseException`, re-raised unchanged after release) is preserved, and a failing `close()` cannot alter either the response or the propagated exception (A10, T5(h)).
- **Internal signature change (HD-1) — approval required:** `PaymentService.__init__` takes a factory instead of a repository. The only in-repo construction site is the test fixture; out-of-tree callers are unknown and would break. Material per governance; a different decision by the human sends the plan back for revision.
- **CON-6 reading (HD-2) — approval required:** adding `release_claim` to the SQLite repository assumes "as-is" means engine and schema. Material per governance (persistence behaviour); a different reading sends the plan back for revision.
- **Persistence/schema/migration:** none. Schema and file format are unchanged, so existing database files remain valid. A rollback is a code-only revert; rows written by the new code are indistinguishable from baseline rows.
- **Rollout/operations:** no configuration, deployment topology or dependency change. Behavioural change: duplicates that previously re-fulfilled now do not; operators will see new WARNING `duplicate callback ignored …` lines (default per HD-4; visible under Python's default WARNING level), plus WARNING/ERROR release and close-failure lines. Duplicates are expected traffic, so a high provider retransmission rate will produce WARNING volume; if that is undesirable the human may choose INFO under HD-4, provided the deployment's logging configuration is confirmed to enable INFO for `payment_service.payment_service`.
- **R1 — Partial side effects on retry:** `fulfil` is not idempotent. A real fulfilment that fails midway then gets re-attempted after release (FR-2), which may repeat partial effects. Releasing on `BaseException` extends this to interrupt-style failures. The stand-in cannot demonstrate this; note for the real implementation.
- **R2 — Release failure:** a failed release leaves the event claimed and unresolved with no recovery (CON-4 class). Mitigation limited to the ERROR log; automatic recovery is out of scope.
- **R3 — Ownership of release:** mitigated by control flow (only the claim winner reaches release); verified by the in-flight and retry tests.
- **R4 — SQLite locking:** concurrent writers may see `OperationalError: database is locked` after 5 s instead of `IntegrityError`; this is handled as a FR-3 claim failure. A rejected duplicate appears to hold an open transaction until its connection closes (see design), so race losers can serialise behind one another; the per-request `close()` in `finally` ends this. Test thread counts are kept small (about 8) and joins are bounded to avoid spurious lock timeouts and hangs.
- **R5 — Test flakiness:** threaded tests use file-backed DBs, `Event`/`Barrier` ordering and bounded joins; no `:memory:` and no sleeps.
- **R6 — Per-request DDL:** the repository constructor runs `CREATE TABLE IF NOT EXISTS` on every request. It is a no-op when the table exists; the test fixture creates the table once before spawning threads to avoid a first-creation race. Accepted overhead for the stand-in; a real deployment would create schema once at startup.
- **R7 — CON-3/CON-1 drift:** guarded by the design rejection of status-based retry paths, the T4 schema-shape test, the T8 AST check, and the authoritative diff read-through.
- **R8 — Governance:** concurrency semantics, persistence behaviour and a component boundary are touched; any deviation from this plan on those points during implementation must be escalated for renewed approval.
- **R9 — Undefined failure-response contract (OQ-1):** handled by preserving baseline propagation (A5, HD-3) rather than inventing a response.
- **R10 — Residual crash window (CON-4):** after the change, the only unrecoverable states are process/host death between claim commit and outcome, a second asynchronous interrupt during release (or close), and a failed release. These are the accepted CON-4 limitation and are documented in the `PaymentService` docstring; no recovery code is added.
- **R11 — Close failure (A10):** a `close()` error is logged at WARNING and swallowed. The residual effect is a possibly lingering SQLite connection/lock until garbage collection; it cannot change the response, the propagated exception, or the claim state.
- **R12 — CON-5 test coupling:** T6(c) only characterizes the acknowledgement and release, so a future reconciliation ticket is not blocked by an assertion of "no recovery".

## Independent plan review

Final independent review evidence is maintained in the sibling `plan-review.json` artifact and is cryptographically bound to this plan's SHA-256. The plan itself is not modified after the passing review merely to embed the review result.
