# Source code review

Reviewed HEAD: `5ea305bc6338f8de095168950a393b2a189b61d9`
Status: **REVIEWED**; coverage: **INCOMPLETE**.

Two confirmed defects introduced by this PR. (1) HIGH/AUTHN_AUTHZ: read_account() in accounts.py no longer checks that the requesting actor owns the account before returning its data — the two-line ownership guard present in base was removed, so any actor can read any other actor's account data; this contradicts the function's own unchanged docstring ('Return an account only to its owner.') and requires human handling as a protected authorization boundary. (2) MEDIUM/CORRECTNESS: _first_items() in pagination.py was changed from items[:limit] to items[:limit + 1], so it now returns one extra item whenever more items exist than the limit, violating its documented 'up to limit items' contract; this is scoped as an automatic-fix candidate (revert to items[:limit]). Coverage limitations: the fixture contains no caller/application code (route handlers, services, etc.) for either function, so the real-world blast radius of both changes — which endpoints or callers actually invoke them, and with what inputs — cannot be confirmed from source alone; both findings are substantiated directly against each function's explicit contract and the diffed logic. Additionally, no test exists for read_account() in either base or head, so no regression test caught or documents the removed ownership check; and the sole existing test for _first_items() (test_pagination.py) only covers the case where len(items) <= limit, which is unaffected by the off-by-one change, so it does not and would not have caught the pagination regression. No new or modified tests accompany this PR for either change.

This review does not approve the PR or execute tests.

## SR-80b4ba7328abb04c — HIGH — HUMAN_REQUIRED

**read_account() no longer verifies caller owns the account before returning its data** — accounts.py:1-3

Trigger: any call to read_account(actor, account) where actor.id != account.owner_id (e.g., actor A supplies actor B's account object). Base code raised PermissionError in that case; head removed the check and unconditionally returns account.data. Consequence: this is a horizontal authorization bypass — any actor can read any other actor's account data, contradicting the function's own docstring ('Return an account only to its owner.'), which was left unchanged. Fix direction: restore the ownership guard (raise PermissionError or an equivalent authorization exception when actor.id != account.owner_id) before returning account.data. Note: no caller/application code is present in this fixture, so the exact endpoints exposed to this bypass can't be confirmed from source alone, and no existing test covers this function in either base or head — the downstream developer should add both a negative test (mismatched actor/owner raises) and a positive test (matching actor/owner returns data) to verify the fix.

Evidence: diff.patch removes the two-line ownership guard from accounts.py; source/base/accounts.py:3-4 contains the guard, source/head/accounts.py contains only the return statement. The docstring 'Return an account only to its owner.' is unchanged in head and directly contradicts the new unconditional-return behavior.
Failure scenario: Any caller invokes read_account(actor, account) where actor.id != account.owner_id (e.g. actor A passing actor B's account object). Base raised PermissionError in this case; head unconditionally executes return account.data with no identity check.
Consequence: Any actor can read any other actor's account.data through this function, a horizontal authorization bypass / unauthorized data disclosure.
Fix direction: Restore the ownership check (raise PermissionError or an equivalent authorization exception when actor.id != account.owner_id) before returning account.data.
Suggested verification: Unit test calling read_account(actor, account) with actor.id != account.owner_id and asserting the appropriate exception is raised, plus a positive-path test with actor.id == account.owner_id asserting account.data is returned.
Routing: High impact; Protected boundary: AUTHN_AUTHZ

## SR-707486f87072c034 — MEDIUM — AUTO_FIX

**_first_items() returns one extra item beyond the documented limit** — pagination.py:1-3

Trigger: calling _first_items(items, limit) whenever len(items) > limit, e.g. _first_items([1,2,3,4,5], 2). Base returned [1,2]; head returns [1,2,3] — one extra element beyond the requested limit. Consequence: any caller relying on this function to cap output at a page size (e.g. pagination) leaks one extra item from the next page into the current result, violating the unchanged docstring contract ('Return up to limit items, preserving order'). Fix direction: revert the slice bound from items[:limit + 1] back to items[:limit]. Note: the one existing test (test_pagination.py, unchanged by this PR) only asserts the len(items) <= limit case and would not catch this regression — the downstream developer should add a test asserting _first_items([1,2,3], 2) == [1, 2] to cover the len(items) > limit case before/after applying the fix.

Evidence: diff.patch changes 'return items[:limit]' to 'return items[:limit + 1]'; source/head/pagination.py:3 confirms the off-by-one slice bound. Docstring 'Return up to limit items, preserving order' is unchanged, establishing the documented contract that is now violated.
Failure scenario: Call _first_items(items, limit) where len(items) > limit, e.g. _first_items([1,2,3,4,5], 2). Base returns [1,2]; head returns [1,2,3], one item beyond the requested limit.
Consequence: Callers relying on _first_items to cap output at limit (e.g. a pagination page-size boundary) receive one extra element whenever more items are available than the limit, leaking the first item of the next page into the current result set.
Fix direction: Revert the slice bound to items[:limit].
Suggested verification: Unit test asserting _first_items([1,2,3], 2) == [1, 2], covering the len(items) > limit case not exercised by the existing test_short_list test.
Routing: All automatic-fix eligibility conditions satisfied

## Coverage gaps

- No test file exists at all for accounts.py/read_account() in the fixture, so the removed ownership check has no accompanying test coverage to evaluate.
- No test file exists for accounts.py/read_account() in the fixture (base or head), so there is no existing regression test that would have caught, or that documents expected behavior around, the removal of the ownership check.
- No test file exists for accounts.py/read_account() in the fixture (base or head), so there is no regression test that could have caught the removed ownership check, and none to evaluate.
- No test file exists for accounts.py/read_account() in the fixture, so there is no regression test that would have caught the removal of the ownership check.
- The fixture contains no caller/application code for read_account() (no route handlers, services, or other call sites), so the full blast radius of the removed ownership check — which endpoints or code paths invoke it, and whether any other layer re-checks ownership — cannot be confirmed from source alone; the finding is substantiated directly against the function's own explicit contract (docstring) and removed guard.
- The fixture contains no caller/application code for read_account() or _first_items() beyond the two function definitions, so the full blast radius (which endpoints/services invoke them, and with what actor/limit values) cannot be confirmed from source alone.
- The fixture contains only accounts.py, pagination.py and test_pagination.py — no application/API/entrypoint code that calls read_account() or _first_items() — so the real-world blast radius (which endpoints or callers pass untrusted actors, or rely on _first_items' limit for a security- or correctness-relevant boundary) cannot be confirmed from source alone.
- The fixture includes no caller/application code for read_account() or _first_items() beyond the two function definitions and one unrelated existing test (test_pagination.py, unchanged), so downstream impact of both changes (e.g., which endpoints or callers pass untrusted actors/limits) cannot be verified from source alone.
- pagination.py's items[:limit + 1] off-by-one change was reviewed against this agent's authorization/concurrency/transaction/resource-lifetime scope and found to have no security or resource-safety dimension (no authz, no shared/concurrent state, no resource acquisition/release involved) beyond a data-correctness deviation from the documented 'up to limit items' contract; it is intentionally not reported here so as not to duplicate the correctness reviewer's lane.
- test_pagination.py (source/head/test_pagination.py) is unchanged by this PR and only asserts _first_items([1], 3) == [1], a case where len(items) <= limit; base and head slices are identical for that input, so this test would not detect the items[:limit+1] regression. No test was added or modified to cover len(items) > limit.
- test_pagination.py exists in both base and head, is unchanged, and only asserts _first_items([1], 3) == [1] (list shorter than limit), a case where items[:limit] and items[:limit+1] are identical; it therefore does not exercise the len(items) > limit case and would not have caught the off-by-one change. No test file was added or modified for either accounts.py or pagination.py in this PR.
- test_pagination.py is unchanged by this PR and only asserts the len(items) < limit case; it does not exercise len(items) > limit and therefore does not and would not catch the off-by-one regression in _first_items().
