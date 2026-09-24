## PAY-DEMO-001

Implements the approved Development Plan (`development-plan.md`, sha256 `a1e213d5291cd2393d2798bb8b2d06d851ee068e70bdaacaf3febcc775291c89`).

**Tasks completed:** T1, T2, T3, T6, T4, T5, T7

**Files changed:**
- `app/payment_service/repository.py`
- `app/payment_service/payment_service.py`
- `app/payment_service/request_handler.py`
- `app/README.md`
- `app/tests/test_repository.py`
- `app/tests/test_payment_service.py`
- `app/tests/test_callback_concurrency.py`

**Assumptions:**
- Duplicate detection compares str(exc) for exact equality with 'UNIQUE constraint failed: processed_callbacks.provider_event_id' in the private static helper _is_duplicate_event, with the message held in a module constant _DUPLICATE_EVENT_MESSAGE, following the plan's sketch.
- On IntegrityError, rollback() is called directly as the plan specifies, so a failure inside that rollback would propagate. For other sqlite3.Error failures (including a failed commit), rollback is attempted and any sqlite3.Error it raises is suppressed, so the original error is re-raised unchanged.
- The CREATE TABLE statement, connection options and constructor signature were left exactly as they were. The change adds no threading, locks, status column or UPDATE statements.
- The original fulfilment exception is captured as 'except Exception as fulfil_error' and re-raised with 'raise fulfil_error', the explicit form the plan permits. A release error raised in the nested try is handled there and is not chained onto the fulfilment exception, because the nested handler has already exited when 'raise fulfil_error' runs.
- record_if_new is called outside any try block, so a claim-write failure other than a duplicate propagates without fulfilment or release, as in the behaviour matrix.
- The processed log line now includes payment_id (A9), and the duplicate line uses the exact A3 message at INFO. The module docstring's 'Known gap' note is replaced by a description of the idempotency, release and release-failure (HD-1/A4 default) behaviour.
- repository_factory is typed as Callable[[str | Path], PaymentRepository] and defaults to the PaymentRepository class. It is called with db_path exactly once per handle() call.
- db_path is typed str | Path to match the PaymentRepository constructor. fulfilment_service is typed FulfilmentService.
- The README note goes just before the test-command section. The documented test command is unchanged.
- A stale request_handler.cpython-310.pyc was already in app/payment_service/__pycache__ with no matching source file. I left it alone.
- Row counts are read through a separate short-lived sqlite3 connection to the same temp-file database rather than the repository's private _conn, so the check is independent of the repository under test.
- The write-lock test relies on the second PaymentRepository's default sqlite3 busy timeout (5s). If rollback did not release the lock, the test fails with OperationalError after that timeout rather than hanging.
- Extra repositories opened in a test are tracked and closed in tearDown along with the primary one, before the temp directory is cleaned up.
- Tests were not run; execution is owned by the workflow.
- The four existing tests and the existing setUp/tearDown are unchanged. New helpers (_db_path, _claim_row_count, _open_repository) and six new test methods were added to the same PaymentServiceTest class.
- Test doubles are defined at module level in the test file only: FailOnceFulfilmentService, AlwaysFailingFulfilmentService, RecordingRepository (a real PaymentRepository that records release_claim and close calls), ReleaseFailingRepository (release_claim raises sqlite3.OperationalError), and ClaimFailingOnceRepository (the first record_if_new call raises sqlite3.OperationalError, then it delegates to the real implementation).
- Claim-row presence is checked through a separate short-lived sqlite3 connection to the same temp-file database, not through the repository's private connection.
- Test (b) checks that the propagated exception is the same object the fulfilment double raised (assertIs) and that an ERROR record containing the provider event ID is logged. It does not assert any __context__ link, because T2 reported that Python does not attach the release error to the propagated fulfilment error.
- Tests (c) and (d) call PaymentService.process_callback directly with a CallbackEvent. In (c), payment_id=None is passed even though the dataclass annotates str, which the dataclass does not enforce.
- Test (f) uses two different provider event IDs for the two consecutive handle() calls and compares each response with the literal controller dict.
- Extra repositories opened in a test are closed through addCleanup. addCleanup runs after tearDown, which has already removed the temp directory. Closing an open SQLite connection after its file is deleted works on Linux.
- The table is created in setUp by constructing one PaymentRepository on the temp-file database and closing it. Each handle() call then opens its own connection through CallbackRequestHandler's default repository factory.
- Test (a) uses a BlockingFulfilmentService double that subclasses FulfilmentService and is defined only in the test file. fulfil() sets a 'started' Event and then waits on a 'release' Event before appending to the ledger. The main thread waits for 'started', sends the duplicate through the same handler, and asserts the response equals {'status': 'ok', 'payment_id': ...} and the ledger is still empty. It then sets 'release' in a finally block, joins the thread and asserts one fulfilment.
- Test (b) uses the existing FulfilmentService, whose ledger relies on list.append being atomic in CPython. The 8 worker threads wait on a threading.Barrier and record responses and exceptions in lists. Log records are captured with assertLogs on payment_service.payment_service at INFO around the start and join of the threads. Duplicate records are counted by a case-insensitive 'duplicate' substring and processed records by the 'callback processed' substring.
- Every Event wait, Barrier wait and thread join uses a 10 s timeout, and the threads are daemon threads, so a failure cannot hang the suite. A thread still alive after its join fails the test explicitly.
- Tests were not run. Execution belongs to the workflow.
- T7 was done as a static review by reading files only. No defects were found in the T1-T6 files, so no source file was changed.
- CON-1/CON-2: a search for 'threading|Lock' in app/payment_service/*.py found nothing. threading is imported only in app/tests/test_callback_concurrency.py, which the plan allows.
- CON-4: repository.py has no status column and no UPDATE statement. The only SQL is CREATE TABLE, INSERT and DELETE. Retry after release deletes the row and inserts it again through the same PRIMARY KEY.
- CON-6: the CREATE TABLE IF NOT EXISTS processed_callbacks statement in repository.py still has the same two columns (provider_event_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL). The connection options and the constructor signature are also unchanged. I compared this by reading only, not byte-for-byte against 109a0a7.
- AC-2/NFR-1: callback_controller.py, models.py and fulfilment_service.py look like the original code from reading. The git status snapshot taken when this session started did not list them as modified. This does not replace the git diff check against 109a0a7.
- Dependencies: every import in app/ is from the stdlib (sqlite3, logging, tempfile, threading, unittest, pathlib, typing, dataclasses) or from the package itself. There is no requirements file under app/. A pyproject.toml exists at the repository root, outside the source roots. I did not check whether it predates this change.
- I read the code against the plan and found these points consistent: record_if_new rolls back on IntegrityError and on any other sqlite3.Error without hiding the original error. process_callback releases the claim on fulfilment failure and re-raises the original exception, and logs at ERROR if the release fails. CallbackRequestHandler opens a repository for each request and closes it in finally. The tests in T4, T5 and T6 match the plan's test lists, and the four existing tests are unchanged.
- The workflow's Python verification stage still has to run: py_compile on app/payment_service/*.py and app/tests/*.py; the full suite (python3 -m unittest discover -t app -s app/tests -v); the concurrency module 20 times in a loop; the two grep checks; and git diff 109a0a7 on callback_controller.py, models.py and fulfilment_service.py.
- Integration was done by reading the actual source against every approved task (T1-T7). No interface mismatches or missing approved work were found, so the integrator made no further file edits. The files listed are those changed by the worker assignments.
- T1/T2 interface: record_if_new(provider_event_id, payment_id) returns True on a new claim, returns False only when str(IntegrityError) equals 'UNIQUE constraint failed: processed_callbacks.provider_event_id' (private helper _is_duplicate_event), rolls back on every failure, and raises for everything else. release_claim(provider_event_id) deletes the row, commits and returns rowcount == 1. payment_service.py uses exactly this contract. The claim call sits outside the try, and the original fulfilment error is re-raised with 'raise fulfil_error'.
- On IntegrityError, record_if_new calls rollback() unguarded, as in the plan sketch. On other sqlite3.Error it attempts rollback and suppresses any rollback error so the original propagates. The CREATE TABLE statement, connection options and constructor signature appear unchanged by reading. No byte-for-byte diff against 109a0a7 was run.
- T2/T3 interface: CallbackRequestHandler(db_path, fulfilment_service, repository_factory=PaymentRepository).handle(body) builds the repository, PaymentService and CallbackController for each call, returns controller.handle(body) unchanged, and closes the repository in finally. This matches the actual CallbackController(payment_service).handle(body) signature. The T4 and T5 tests use this handler exactly as defined.
- Log messages in payment_service.py match plan A3/A4, and the tests assert against them. Duplicate: INFO 'duplicate callback ignored for provider_event_id=%s payment_id=%s'. Processed: INFO 'callback processed for provider_event_id=%s payment_id=%s', which does not contain 'duplicate'. Released: WARNING 'fulfilment failed; claim released for retry ...'. Release failure: logger.exception at ERROR.
- Static checks by reading and searching: the case-sensitive plan grep 'threading|Lock' finds no match in app/payment_service/*.py. The only lowercase 'lock' hits are docstring prose in repository.py. repository.py contains no 'UPDATE' or 'status'. callback_controller.py, models.py and fulfilment_service.py match the baseline content described in the plan. Every import is stdlib or package-local, and app/ has no requirements file.
- A stale app/payment_service/__pycache__/request_handler.cpython-310.pyc and test .pyc files exist in __pycache__ directories. They were left untouched because they are build byproducts and not source.
- No tests, py_compile, grep, loops or git commands were executed. All execution evidence belongs to the workflow's Python verification stage.

**Deviations:**
- A4 says the release error 'remains attached as the exception's __context__'. Python does not do that with the plan's sketch (bare raise) or with the permitted 'raise fulfil_error' form. It is the other way round: the release error's __context__ is the fulfilment error, and the fulfilment error that propagates has no link to the release error. The release error and its traceback are recorded only by logger.exception. I did not add code to attach it manually, because the task instructions do not ask for that. A test that asserts the __context__ link would fail.
- This worker has no shell or git access, so it did not run the parts of T7 that need them: the full suite, the 20-run concurrency loop, py_compile, grep and git diff against 109a0a7. The Python verification stage owns them.
- Carried over from T2 without changes: the plan's A4 says the release error stays attached as the propagated exception's __context__. Python links them the other way round. The release error's __context__ is the fulfilment error, and the release error is recorded only through logger.exception. No test asserts the __context__ link. I did not add code to attach it, because that would go beyond the plan's task list.
- Plan A4 says the release error 'remains attached as the exception's __context__'. Python does not produce that link with the plan's sketch or with the permitted 'raise fulfil_error' form. The release error's __context__ is the fulfilment error, and the propagated fulfilment error carries no reference to the release error, which is recorded only through logger.exception (ERROR with traceback). Adding code to attach it manually was not done because the task list does not ask for it. No test asserts this link. The rest of the HD-1/A4 default is implemented as planned: log at ERROR, re-raise the original fulfilment error, and leave the event claimed.
- The executable parts of T7 were not run by the implementer, who has no shell or git authority: the full suite, the 20-run concurrency loop, py_compile, the grep checks and 'git diff 109a0a7' on callback_controller.py, models.py and fulfilment_service.py. The workflow's Python verification stage owns them.

**Verification:**
- `python3 -m compileall -q app` — PASS (exit 0)
- `python3 -m unittest discover -t app -s app/tests -v` — PASS (exit 0)

**PR HEAD SHA:** `efa6055ca0695596e220478e0c6d1df99049b7ac`

---
This PR was prepared by the agentic delivery workflow. Final approval must be granted by a human reviewer in source control, not by any agent.
