# Planning Analysis

Ticket PAY-DEMO-001, repository baseline `a806a98e5a4b024ef5000d29a3458f7c219bc068`, branch `feature/slim-agentic-sdlc`. No developer guidance file was supplied. The `app/` tree is unmodified in `git status`, so the code below is the baseline. I had no shell tool, so I did not run the tests. Statements about test outcomes are inferred from reading the code.

## Context Summary

The validated Planning Context (`planning-context-v1.json`, sources JIRA:PAY-DEMO-001, CONF:4812, CONF:4934; no contradictions, no retrieval warnings) requires that a repeated provider event ID does not fulfil twice. The requirements:

- **Claim and release:** a claim persisted under a uniqueness constraint (FR-6, NFR-2, C-3) is released, e.g. by deleting the row, when fulfilment fails after the claim, so a retry can re-claim (FR-3). A failure to claim in the first place is not a retry scenario (FR-4).
- **In-flight duplicates:** a duplicate that arrives while the original is in flight must not fulfil (FR-5). It gets the same successful response as any other duplicate (FR-7).
- **Response contract:** it is unchanged (AC-2, NFR-1).
- **Logging:** duplicates are distinguishable in logs (AC-3, FR-8, NFR-3).

Constraints:
- **C-1:** no distributed lock.
- **C-2:** reuse existing persistence.
- **C-3:** every idempotency write goes through the uniqueness constraint. A status compare-and-swap is not acceptable.
- **C-4:** `PaymentRepository` is constructed per request.
- **C-5:** keep the SQLite stand-in; no PostgreSQL.
- **C-6, C-7:** accepted limitations (crash recovery, and an in-flight duplicate ack'd before the claimant fails).
- **C-8:** provider adapter public interfaces are unchanged.

Five non-blocking open questions (OQ-1 to OQ-5) are carried forward below.

## Repository Evidence

The relevant code is a small stdlib-only Python fixture: 4 source modules plus `models.py`, with one test module. Facts:

- **`PaymentService.process_callback`** (`app/payment_service/payment_service.py:23-27`) calls `self._repository.record_if_new(...)` and discards the boolean return value. It then unconditionally calls `fulfil()`, logs `"callback processed for provider_event_id=%s"`, and returns `ProcessingResult(payment_id, fulfilled=True)`.
  - There is no duplicate branch, no `try`/`except`, and no release of a claim.
  - The module docstring (lines 3-5) states the known gap: a duplicate re-fulfils.
  - Baseline behaviour therefore violates AC-1 and AC-3.
- **`PaymentRepository`** (`app/payment_service/repository.py`):
  - The constructor opens one `sqlite3.connect(db_path)` and runs `CREATE TABLE IF NOT EXISTS processed_callbacks (provider_event_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL)` followed by `commit()` (lines 18-28).
  - `record_if_new` does `INSERT` and `commit()`, returns `True`, and returns `False` on `sqlite3.IntegrityError` (lines 30-40). Other `sqlite3` errors propagate, which is consistent with FR-4.
  - The uniqueness primitive already exists (PRIMARY KEY), which fits FR-6, C-2 and C-3.
  - There is no delete/release method, no status column and no completion marker.
  - The claim is committed before any fulfilment happens. This matches the C-6 crash window.
  - `close()` exists (line 42).
- **`CallbackController.handle`** (`app/payment_service/callback_controller.py:18-24`):
  - It builds a `CallbackEvent` from `body["provider_event_id"]` and `body["payment_id"]`, calls the service, and returns `{"status": "ok", "payment_id": result.payment_id}`.
  - The response is independent of `ProcessingResult.fulfilled`.
  - There is no exception handling and no status code. Missing keys raise `KeyError`, and any service exception propagates.
  - The module docstring already names AC-2.
- **`FulfilmentService`** (`app/payment_service/fulfilment_service.py`) is an in-memory ledger. `fulfil()` appends to a list and never raises. `fulfilment_count()` supports assertions.
- **`models.py`** has frozen dataclasses `CallbackEvent(provider_event_id, payment_id)` and `ProcessingResult(payment_id, fulfilled)`.
- **Wiring:**
  - No composition root exists in the repo. `main.py` is an unrelated PyCharm stub, and `app/payment_service/__init__.py` is empty.
  - Objects are wired only in the tests (`app/tests/test_payment_service.py:13-19`), where one `PaymentRepository` is built in `setUp` and injected into `PaymentService`, then into `CallbackController`.
  - `PaymentService` takes a repository instance in its constructor (`payment_service.py:19-21`).
  - There is currently no place where a repository is "constructed per request" (C-4).
- **Provider adapters:** a grep for adapter/provider modules found none under `app/`. Only `provider_event_id` appears. C-8 is trivially satisfied so long as no new public interface is added, and there is nothing to break.
- **Logging:** each module has `logging.getLogger(__name__)` only in `payment_service.py` (logger name `payment_service.payment_service`). No logging configuration, format or log-level policy exists in `app/`.

## Existing Patterns and Relevant Tests

- **Layering:** Controller, then Service, then Repository plus Fulfilment. Constructor injection, `from __future__ import annotations`, frozen dataclasses, and docstrings that cite ticket and AC ids.
- **Repository style:** raw `sqlite3`, one method per operation, commit inside each method, and `IntegrityError` translated to a boolean.
- **Logging style:** module logger with lazy `%s` formatting, `logger.info`.
- **Tests:** `app/tests/test_payment_service.py`, `unittest`, temp-dir SQLite file per test (`setUp`/`tearDown` closes the repository).
  - Run with `python3 -m unittest discover -t app -s app/tests -v` (`app/README.md:11-15`).
  - The four tests, and what I inferred from the code (not executed):
    - `test_first_callback_fulfils_once` should pass at baseline.
    - `test_duplicate_callback_does_not_refulfil` should fail at baseline because the boolean is ignored (AC-1).
    - `test_response_shape_is_identical_for_new_and_duplicate` should pass at baseline. It pins `first == second`, so the duplicate response must carry the same `payment_id`.
    - `test_duplicate_is_distinguishable_in_logs` uses `assertLogs("payment_service.payment_service", level="INFO")` and asserts the substring `"duplicate"` (case-insensitive). It should fail at baseline. It pins the logger name and a level at or above INFO. It is a repo test, not a source requirement, so it only partly informs OQ-2.
- **Gaps in existing tests:**
  - There is no test for release-on-failure (FR-3), because there is no failing `FulfilmentService` double.
  - There is no test for a claim failure that is not a duplicate (FR-4).
  - There is no concurrent-duplicate test (FR-5, FR-6, NFR-2).
  - There is no test for a different connection per request (C-4), and no test for an in-flight duplicate (FR-7).
- The repo-level `tests/` directory covers the SDLC workflows, not the payment app, and is unrelated to this ticket's code.

## Likely Change Areas

- **`app/payment_service/payment_service.py`** (primary):
  - Consume the claim result and branch: duplicate goes to a distinguishable log and a skip of fulfilment; new goes to fulfil.
  - Handle a fulfilment failure by releasing the claim and re-raising.
  - Preserve the same response for duplicates, with `payment_id` taken from the event.
- **`app/payment_service/repository.py`:**
  - It needs a release operation, such as a delete of the claim row, that goes through the same table and constraint.
  - It may need connection-lifecycle changes for per-request construction.
- **Wiring for C-4:**
  - Some per-request construction path must be created, for example a repository factory injected into the service or controller, or the service opening a repository per call.
  - This touches the constructor signatures of `PaymentService` and possibly `CallbackController`, which are not provider adapters.
  - The existing test `setUp` would need adjusting.
- **`app/payment_service/callback_controller.py`:** likely unchanged apart from possibly docstrings, since the response is already independent of `fulfilled`.
- **`app/tests/test_payment_service.py`:** new tests for the gaps listed above.
- **`FulfilmentService`:** unchanged. Tests need a failing double, and the ledger must stay shared across per-request repositories.
- **Docstrings:** the "Known gap" note in `payment_service.py` and the `repository.py` docstring may be stale afterwards.

## Dependencies

- Stdlib only (`sqlite3`, `logging`, `unittest`). No dependency manifest was inspected, and the code needs no new dependency for any of the above.
- The `sqlite3` standard-library defaults matter (from general knowledge of the module, not from repo files):
  - `check_same_thread=True`, so a single connection cannot be used across threads. The driver already forbids sharing one repository across request threads.
  - A default 5-second busy timeout on file databases.
  - Implicit transactions on DML.
- Concurrency tests need a file-backed database path shared across repository instances. `:memory:` would give each connection a separate database.
- Downstream components are `CallbackController`, `PaymentService` and `FulfilmentService`. There is no external provider or HTTP framework in the repo.

## Requirement-to-Code Mapping

| Requirement | Current state | Evidence |
|---|---|---|
| AC-1, FR-2 | Not met: the claim result is ignored and `fulfil()` always runs | `payment_service.py:24-25` |
| FR-6, NFR-2, C-3 | Uniqueness constraint exists (PRIMARY KEY); no release path, so the retry-after-failure write does not exist yet | `repository.py:22-24, 33-40` |
| FR-3 | Not met: no failure handling and no release; a failed fulfilment would leave a claim row that blocks retries once duplicates are honoured | `payment_service.py:23-27`; `repository.py` has no delete |
| FR-4 | Consistent: only `IntegrityError` is swallowed; other storage errors propagate | `repository.py:39-40` |
| FR-5 | Structurally supported, because the claim is committed before fulfilment, so a duplicate sees `IntegrityError` immediately | `repository.py:33-37` |
| FR-7, AC-2, NFR-1 | The response is built only from `result.payment_id`; the duplicate response must therefore keep `payment_id` | `callback_controller.py:23-24`; test `test_response_shape_is_identical…` |
| AC-3, FR-8, NFR-3 | Not met: only a "callback processed" line is logged; there is no duplicate marker | `payment_service.py:26` |
| C-4 | Not met: one repository is constructed per test and injected; no per-request construction exists | `test_payment_service.py:15-18`; `payment_service.py:19-21` |
| C-1, C-2, C-5 | Compatible: SQLite constraint, no lock, no PostgreSQL | `repository.py:1-16` |
| C-6 | Matches the current claim-then-fulfil order: a crash between claim and outcome leaves the row | `repository.py:33-38`; `payment_service.py:24-25` |
| C-7 | No repo behaviour yet; a consequence of FR-3 plus FR-7 | n/a |
| C-8 | No provider adapter code exists in `app/` | grep, no matches |

## Ambiguities and Conflicts

No conflict between the Planning Context and the repository, beyond the baseline not yet implementing the behaviour. Open questions carried forward from the Planning Context, with repository observations (not authority):

- **OQ-1 (response values):** the repo has no HTTP status code. The controller returns a dict `{"status": "ok", "payment_id": ...}` for both first delivery and duplicate (same code path). A failure currently propagates as an exception with no defined response. The current failure behaviour is undefined in the repo, and the sources do not say what a failed delivery should return.
- **OQ-2 / OQ-5 (log content and strength):** no log format is defined. The existing test requires the substring "duplicate" at INFO on logger `payment_service.payment_service`.
- **OQ-3 (scope beyond fulfilment):** the repo has no payment state transitions. `PaymentService` only records and fulfils, so today "payment processing" equals fulfilment.
- **OQ-4 ("completed" and retention):** the schema has no status or completion marker. A row's presence means claimed-or-completed, and rows are never removed. In-flight and completed events are indistinguishable, which is sufficient for FR-5 and FR-2 via the constraint alone. Adding a column would need a schema migration, because `CREATE TABLE IF NOT EXISTS` does not alter an existing table (I did not check for an existing checked-in database file).
- **Unspecified in sources:**
  - A duplicate carrying the same event ID but a different `payment_id`. The repository ignores the second `payment_id`.
  - Whether the release operation itself failing (storage error) is covered by the C-6 limitation. It would leave a stuck claim.

## Risks

- **Release ownership:** only the request that won the claim may release it. A rejected duplicate that failed or released would delete the real claimant's row and re-open double fulfilment.
- **Order of operations:** if a plan moved fulfilment inside the claim transaction (an attempt to use "transaction infrastructure", C-2), a concurrent duplicate on a file SQLite database would block on the write lock up to the default 5-second timeout and then raise `OperationalError` (not `IntegrityError`). That would break FR-5 and FR-7. Committing the claim before fulfilment, as now, avoids that.
- **Per-request DDL:** `PaymentRepository.__init__` runs `CREATE TABLE IF NOT EXISTS` plus `commit()` on every construction. Under per-request construction that runs on every request and can contend for the write lock. This is an inference and needs verification in a concurrent test.
- **Release failure:** a failing release, or a crash, leaves the event unresolved. C-6 explicitly accepts a crash. The release-failure case is not explicitly covered.
- **Test gap:** concurrency correctness (FR-5, FR-6, NFR-2) is not verified by the current tests, and a naive single-thread "duplicate" test does not exercise the race.
- **Signature change:** per-request construction (C-4) likely changes the `PaymentService` and possibly `CallbackController` constructors and every existing test setup. These are internal, not provider adapters (C-8), but that is a material design choice for the Plan Author.
- **Prior runs:** `sdlc-records/PAY-DEMO-001/candidates/plan-PAY-DEMO-001-20/` holds a prior NOT_CONVERGED candidate for this ticket. I did not read it, and it has no authority over this analysis.

## Evidence Index

- `app/payment_service/payment_service.py:1-27`: known-gap docstring, ignored `record_if_new` result, unconditional fulfilment, "callback processed" log.
- `app/payment_service/repository.py:1-43`: sqlite3 stand-in, PRIMARY KEY on `provider_event_id`, commit-per-insert, `IntegrityError` to `False`, no release method, `close()`.
- `app/payment_service/callback_controller.py:18-24`: response shape `{"status": "ok", "payment_id": ...}`, no error handling.
- `app/payment_service/fulfilment_service.py:5-17`: in-memory ledger, `fulfil()` never raises.
- `app/payment_service/models.py`: `CallbackEvent`, `ProcessingResult`.
- `app/tests/test_payment_service.py:13-50`: four tests, single shared repository in `setUp`, logger name and "duplicate" assertion.
- `app/README.md:9-15`: test command.
- `main.py`: unrelated PyCharm stub, no composition root.
- Grep for `CallbackController|PaymentService|PaymentRepository|FulfilmentService|provider_event|event_id` across the repo: app hits only under `app/payment_service/` and `app/tests/`; other hits are SDLC records, examples, archived docs and test fixtures.
- Context: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-21/context/normalized/planning-context-v1.json`; raw sources `context/raw/sources/{JIRA_PAY-DEMO-001,CONF_4812,CONF_4934}.md` (checked against the normalized requirements; digests in `retrieval.json` match the normalized `sources[]` entries).
- Workflow: `.agentic-sdlc/contracts/planning-workflow.md`, `.agentic-sdlc/policies/governance.md`.
