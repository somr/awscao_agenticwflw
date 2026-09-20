# Planning Analysis

Ticket: PAY-DEMO-001 · Baseline SHA: a806a98e5a4b024ef5000d29a3458f7c219bc068 · Planning Context: `planning-context-v1.json` (schema 1.0, no contradictions, no retrieval warnings, 4 non-blocking open questions).

Method note: this analysis is static. Bash is disabled in this session, so **no tests were executed**. Statements about test pass/fail below are inferences from reading the code. `examples/PAY-DEMO-001/records/*` holds artifacts from an earlier run for the same ticket ID (plan, diff, review). I matched them by grep only and deliberately did not use them as baseline evidence.

## Context Summary

- **Problem:** the provider may resend a callback. A repeated provider event must not cause duplicate fulfilment (AC-1, FR-1).
- **Response contract:** it must stay unchanged (AC-2, FR-7). This includes a duplicate that arrives while the original is still in flight (FR-5).
- **Logging:** a duplicate must be distinguishable from a first delivery in the logs (AC-3, FR-6, NFR-3).
- **Mechanism (CON-3, NFR-1):** every idempotency-relevant write, including a retry after failure, must go through the persistence-layer uniqueness constraint on the provider event ID. A status-based compare-and-swap is explicitly disallowed. There is no application-level or distributed lock (CON-1).
- **Failure handling:**
  - If fulfilment fails after the claim, the claim must be released, for example by deleting the row (FR-2).
  - If the claim write itself fails for a non-duplicate reason, no released-claim bookkeeping is needed (FR-3).
- **Per-request repository (NFR-2):** `PaymentRepository` is constructed per request, with a fresh connection to the same store.
- **Accepted limitations (CON-4, CON-5):** a crash between acceptance and durable outcome, and a payment whose in-flight original fails after a duplicate was already acknowledged, are not recovered automatically.
- **Out of scope:** provider adapter public interfaces (CON-7), a real PostgreSQL dependency, and reconciliation jobs (CON-6).

## Repository Evidence

All application code lives under `app/payment_service/`. It has no HTTP framework, no composition root and no entry point. The only place the classes are wired together is the test `setUp`.

**F1. The duplicate bug is in `PaymentService.process_callback`.**
- `app/payment_service/payment_service.py:23-27` calls `self._repository.record_if_new(...)` and discards its boolean return value.
- It then always calls `fulfil(...)`, logs `"callback processed for provider_event_id=%s"`, and returns `ProcessingResult(fulfilled=True)`.
- So AC-1 and AC-3 are unmet on baseline.
- The module docstring (`payment_service.py:3-5`) records this as the "Known gap (tracked by PAY-DEMO-001)".

**F2. The uniqueness constraint already exists (this answers OQ-3).**
- `app/payment_service/repository.py:20-27` creates `processed_callbacks(provider_event_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL)` with `CREATE TABLE IF NOT EXISTS`.
- `record_if_new` (`repository.py:30-40`) inserts, commits and returns `True`.
- It catches only `sqlite3.IntegrityError` and returns `False`. Any other `sqlite3` error, such as `OperationalError`, propagates. That already matches FR-3 for non-duplicate claim failures.
- The module docstring (`repository.py:1-9`) states the design intent: a UNIQUE constraint, not a lock.

**F3. The repository has no status column and no release operation.**
- Its only methods are `__init__`, `record_if_new` and `close`.
- A row means only "claimed". It cannot express in-flight, completed or failed.
- In-flight and completed events are indistinguishable in storage.
- Nothing in the repository can delete a claim (FR-2).
- Because `CREATE TABLE IF NOT EXISTS` is used, any column added later would not migrate a database file that already exists.

**F4. The repository is single-connection and per-instance.**
- `PaymentRepository.__init__` opens `sqlite3.connect(str(db_path))` and keeps it in `self._conn`.
- The default `check_same_thread=True` is not overridden, so sharing one instance across threads would raise `sqlite3.ProgrammingError`.
- `db_path` must be a file path for two connections to see the same store. A `:memory:` path gives each connection its own database.
- `PaymentService.__init__` (`payment_service.py:19-21`) stores one repository instance for its lifetime.
- `CallbackController.__init__` stores one `PaymentService`.
- NFR-2 (per-request repository) therefore cannot be met by the current wiring pattern of a service that holds a repository. No factory or per-request construction site exists anywhere in `app/`.
- The sqlite3 connection timeout is not set, so it defaults to 5 seconds. Concurrent writers can hit `OperationalError: database is locked` instead of `IntegrityError`.

**F5. The response contract is a plain dict, and `fulfilled` is unused.**
- `CallbackController.handle` (`callback_controller.py:18-24`) builds a `CallbackEvent` from `body["provider_event_id"]` and `body["payment_id"]`.
- It returns `{"status": "ok", "payment_id": result.payment_id}`.
- There is no HTTP status code or body anywhere in the repository.
- `ProcessingResult.fulfilled` (`models.py:13-16`) is not read by the controller.
- The controller has no exception handling, so an exception from fulfilment propagates out of `handle`. No response is defined for a failed fulfilment.
- The `result.payment_id` in the response comes from the incoming event, not from stored data.

**F6. `FulfilmentService` is a stand-in with no failure path.**
- `fulfil` appends to an in-memory `ledger` list (`fulfilment_service.py:13-14`).
- `fulfilment_count` exposes the count for test assertions.
- It never raises, and it is not itself idempotent.

**F7. Logging exists but cannot tell a duplicate from a first delivery.**
- `payment_service.py:15` sets `logger = logging.getLogger(__name__)`, which resolves to `payment_service.payment_service`.
- The only log line today is the INFO `"callback processed ..."`, emitted for every delivery.
- Nothing is logged on failure.

## Existing Patterns and Relevant Tests

- **Framework and command:** `unittest`. The command is in `app/README.md:9-15`: `python3 -m unittest discover -t app -s app/tests -v`. `pyproject.toml` exists, but a grep for pytest or unittest configuration in it found nothing.
- **Layout:** the single test module is `app/tests/test_payment_service.py`. It imports `payment_service.*` as a top-level package, which relies on the `-t app` option.
- **Fixture pattern:**
  - `setUp` creates a `tempfile.TemporaryDirectory` and a file-backed `PaymentRepository`.
  - It builds `FulfilmentService`, `PaymentService` and `CallbackController` around that one shared repository.
  - `tearDown` closes the repository and cleans up the directory.
  - The `setUp` composition is the only wiring in the repo and uses one shared repository, which conflicts with NFR-2.
- **Existing tests, and what they assert:**
  - `test_first_callback_fulfils_once` asserts the response `{"status": "ok", "payment_id": "pay-1"}` and one fulfilment.
  - `test_duplicate_callback_does_not_refulfil` asserts a count of 1 after two identical callbacks (AC-1).
  - `test_response_shape_is_identical_for_new_and_duplicate` asserts the responses are equal (AC-2).
  - `test_duplicate_is_distinguishable_in_logs` uses `assertLogs("payment_service.payment_service", level="INFO")` and asserts that the lowercased output contains `"duplicate"` (AC-3).
- **Inferred baseline status, not executed:** the two tests for AC-1 and AC-3 should fail today, because of F1. The first-callback and response-shape tests should pass.
- **Coverage gaps, none of which are tested today:**
  - fulfilment raising and the claim being released (FR-2);
  - a claim-write failure that is not a duplicate (FR-3);
  - a concurrent duplicate racing at the persistence layer (FR-4, NFR-1, NFR-2);
  - retry after release;
  - the response for a duplicate that arrives in flight (FR-5).
- **Test-support note:** `FulfilmentService` has no way to raise. Tests for FR-2 would need a subclass or stub. `FulfilmentService` is not a provider adapter, so this appears to be in scope, but the plan should make it explicit.

## Likely Change Areas

Evidence-based candidates only. This is not an implementation design.

1. `app/payment_service/payment_service.py`
   - `process_callback` must act on the claim result.
   - Duplicate path: skip fulfilment and log the duplicate.
   - Failure path: release the claim when fulfilment fails.
   - The stale "Known gap" docstring would need updating.
2. `app/payment_service/repository.py`
   - A claim-release operation that goes through the same constraint (F3).
   - Depending on OQ-4, possibly state tracking.
   - CON-6 says to keep the SQLite implementation "as-is", so how far this file may change is an interpretation (see Ambiguities).
3. Wiring or construction of `PaymentRepository` per request (NFR-2, F4).
   - No composition root exists.
   - The candidates are how `PaymentService` and `CallbackController` obtain a repository per request, and how the test fixture constructs them.
   - This may alter constructor signatures.
   - Constructor signatures of `PaymentService` and `CallbackController` are internal, not provider adapter interfaces (CON-7), but that is an inference.
4. `app/payment_service/callback_controller.py`
   - Probably not needing behavioural change, since the response is already uniform across new and duplicate.
   - It is affected only if the duplicate-vs-first outcome needs to be surfaced through `ProcessingResult`, and only if the response shape stays identical.
   - Exception behaviour on failed fulfilment is currently unspecified (OQ-1).
5. `app/payment_service/models.py`
   - `ProcessingResult` may need a duplicate indicator. Nothing requires this. It is an option only.
6. `app/tests/test_payment_service.py`
   - New tests for the coverage gaps listed above.
   - Concurrency tests need per-thread repositories against one file-backed database.
7. `app/payment_service/fulfilment_service.py`: test-only failure injection, if needed.

## Dependencies

- **Runtime:** Python 3.10 (the cached `.pyc` files are `cpython-310`) and the stdlib `sqlite3`, `logging`, `dataclasses`, `pathlib`. There are no third-party dependencies.
- **Internal call chain:** `CallbackController` calls `PaymentService.process_callback`, which uses `PaymentRepository.record_if_new` and then `FulfilmentService.fulfil`. This matches the CONF:4934 statement that `PaymentService` coordinates state transitions and fulfilment.
- **Storage:** a file-backed SQLite database shared across connections. The uniqueness guarantee depends on the `PRIMARY KEY` on `provider_event_id` staying in place.
- **External assumption (CONF:4812):** the provider event ID is stable across retransmissions. This cannot be verified from the repository.
- **Not present:** no HTTP framework, provider adapters, configuration files, migrations or other persistence users under `app/`.

## Requirement-to-Code Mapping

| Requirement | Current state | Evidence |
|---|---|---|
| AC-1 / FR-1 | Not met. The boolean from `record_if_new` is ignored and fulfilment always runs. | F1 |
| FR-2 (release on fulfilment failure) | Not met. No try/except, no release method. | F3, F1 |
| FR-3 (non-duplicate claim failure) | Partly consistent already. Only `IntegrityError` is swallowed, so other errors propagate. | F2 |
| FR-4 / NFR-1 (constraint-enforced concurrency) | The constraint exists (PRIMARY KEY). The result is unused, and no concurrency test exists. | F2, F1 |
| FR-5 / AC-2 / FR-7 (response contract) | The response dict is identical for new and duplicate. It does not depend on `fulfilled` or on claim outcome. | F5 |
| AC-3 / FR-6 / NFR-3 (log distinguishability) | Not met. One INFO line is emitted for every delivery. | F7 |
| NFR-2 (per-request repository) | Not met. The instance is long-lived and injected, and no per-request construction exists. | F4 |
| CON-1 (no lock service) | Not violated today. | F2 |
| CON-2 (reuse persistence) | The existing repository is the natural reuse point. | F2, F3 |
| CON-3 (no status CAS) | Not violated today. There is no status column, and a release via delete-then-reinsert fits the existing schema. | F2, F3 |
| CON-4, CON-5 | Accepted limitations. No repository code provides recovery. Nothing to implement. | F3 |
| CON-6 (keep SQLite as-is) | The existing SQLite implementation is present. | F2 |
| CON-7 (provider adapters out of scope) | No provider adapter exists in the repository. | Evidence Index |

## Ambiguities and Conflicts

Carried forward from the Planning Context and updated with repository evidence.

- **OQ-1 (response contract).** Repository evidence gives a partial answer. The only "response" is the dict `{"status": "ok", "payment_id": <id from the incoming body>}`. There is no status code. The controller propagates exceptions on failed fulfilment, so the failure-case response is undefined, not documented. Whether "unchanged" includes propagating that exception is a question for humans.
- **OQ-2 (log format).** Still unresolved. The existing test requires `"duplicate"` to appear, case-insensitively, at INFO or above on logger `payment_service.payment_service`. That is the only concrete constraint in the repository. It is derived from a test, not from a source requirement.
- **OQ-3 (constraint exists?).** Resolved by F2: a PRIMARY KEY on `provider_event_id` already exists, so it does not need to be added.
- **OQ-4 (states).**
  - Repository evidence: no status is stored, and a row means only "claimed".
  - Consequence: a row cannot tell "completed" from "in flight" (FR-1 vs FR-4).
  - The sources do not say whether the two cases need different logging or responses.
- **Interpretation risk, CON-6 "as-is" vs releasing claims.** A release operation requires new repository behaviour. Whether adding a method counts as "as-is", as opposed to only the schema and database engine staying unchanged, is not stated. No schema change is needed for delete-then-reinsert.
- **Interpretation risk, NFR-2 vs constructor injection.** CONF:4934 says `PaymentRepository` must be constructed per request. The current design injects one instance into `PaymentService`. Meeting NFR-2 changes how `PaymentService` gets its repository, and there is no existing example of that in the repository.
- **Duplicate `payment_id` mismatch.** The response echoes the duplicate's own `payment_id`, not the stored one. If a duplicate carried a different `payment_id` under the same event ID, the sources say nothing about the expected behaviour.
- **No contradictions** were found between the Planning Context and the repository. The Planning Context also has no contradictions of its own.

## Risks

- **R1. Partial side effects on retry.** `fulfil` is not idempotent. If a real fulfilment fails after some side effects, FR-2's release-and-retry could repeat them. The stand-in cannot show this, but the design intent (FR-2) makes it relevant.
- **R2. Release itself can fail.** If the release after a fulfilment failure fails, the row stays claimed with no recovery. This matches the accepted CON-4 limitation, but the plan should say how it is surfaced.
- **R3. Wrong-owner release.** A release keyed only by `provider_event_id` could delete a claim belonging to another delivery if ownership is not considered. Under the constraint, only the original claimant should reach the release path. This should be verified in the plan.
- **R4. SQLite locking differences.** Concurrent writers may get `OperationalError: database is locked` after the default 5-second wait. This is a claim failure that is not a duplicate (FR-3). It is not the same as the PostgreSQL behaviour that SQLite stands in for.
- **R5. Test flakiness.** Concurrency tests must use a file-backed database and separate connections per thread. A `:memory:` path would make each connection independent.
- **R6. Existing-database compatibility.** `CREATE TABLE IF NOT EXISTS` will not add columns to an existing file. Adding a status column (if chosen under OQ-4) would break older database files.
- **R7. CON-3 non-compliance if a status-based approach is chosen.** Any retry path that uses `UPDATE ... WHERE status = 'failed'` violates CON-3 as written.
- **R8. Governance.** Changes to transaction or concurrency semantics and to persistence behaviour are "material" under `governance.md`. Any deviation from the approved plan on those points would require escalation and renewed approval.
- **R9. Missing failure-case contract.** OQ-1 leaves the failed-fulfilment response undefined. Any choice made without a human answer is an invented requirement.

## Evidence Index

- `app/payment_service/payment_service.py:3-5, 15, 19-21, 23-27`: known-gap docstring; logger; injected repository; the `process_callback` flaw (F1, F4, F7).
- `app/payment_service/repository.py:1-9, 17-28, 30-40, 42-43`: constraint design intent; schema; single connection; `record_if_new`; `close` (F2, F3, F4).
- `app/payment_service/callback_controller.py:1-5, 14-24`: unchanged-response requirement in the docstring; response dict; no exception handling (F5).
- `app/payment_service/fulfilment_service.py:5-17`: in-memory ledger, no failure path (F6).
- `app/payment_service/models.py:7-16`: `CallbackEvent`, `ProcessingResult.fulfilled` (F5).
- `app/tests/test_payment_service.py:13-51`: fixture pattern; four existing tests (Existing Patterns).
- `app/README.md:9-15`: test command; fixture description.
- `app/payment_service/__init__.py`: empty. No wiring, no exports.
- Repo-wide grep of `app/` for `PaymentRepository(`, `CallbackController(`, `threading` and `Thread`: the only construction site is `test_payment_service.py:16, 19`. No threading anywhere (F4).
- Repo-wide search for `*payment*` and a listing of `app/**/*`: there are no provider adapter files. `pyproject.toml` exists, but a grep for `testpaths|pythonpath|unittest|pytest` found no matches.
- Planning Context: `.agentic-sdlc/runtime/PAY-DEMO-001/plan-PAY-DEMO-001-18/context/normalized/planning-context-v1.json`.
- Raw sources checked for provenance (`context/raw/sources/`): `JIRA_PAY-DEMO-001.md`, `CONF_4812.md`, `CONF_4934.md`. Their content matches the normalized context, including the FR/NFR/CON texts, the accepted limitations and scope. The digests in `retrieval.json` equal those in the Planning Context.
- Contracts: `.agentic-sdlc/contracts/planning-workflow.md`, `.agentic-sdlc/policies/governance.md` (material-change guidance, R8).
