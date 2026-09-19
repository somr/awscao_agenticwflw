# Modular workflow verification

Baseline: `20b7cd5`, pushed to `origin/main` before refactoring.

## Compatibility boundaries

- Inputs, registered names, profiles, write guards, artifact paths, review policies,
  timing/retry constants and CAO step identities remain unchanged.
- Shared helpers now have one maintained implementation. Delivery explicitly retains
  its existing JSON-repair prompt variant; Planning and Source Review retain theirs.
- The only shared error-text change is "headless workflow agents" in place of a
  workflow-specific label. Exception types and failure handling remain unchanged.
- Source Review still refuses reuse of an existing run directory. The bundler does
  not change that lifecycle or introduce automatic fixing/publication.

## Regression and integration checks

The complete suite passed using CAO's Python 3.14 interpreter:

- **140 tests passed** against generated deployment bundles.
- **140 tests passed** against maintained source modules (`SDLC_TEST_SOURCE=1`).
- Python compilation, shell syntax, documentation links and Git whitespace checks passed.

Python 3.14 emitted a non-failing `HTTPError` resource warning during the existing
Atlassian error-response fixture. That adapter's function body is unchanged.

Existing assertions were retained. Common runtime assertions moved to their own class;
`SDLC_TEST_SOURCE=1` selects source modules rather than deployment bundles. Added tests
cover reproducible builds, dependency manifests, module binding/import errors, hidden
package initialization, isolated-interpreter execution, relocated snapshots, changed
or removed source dependencies, and installer failure/collision/backup handling.

Full planning/delivery integration runs in both forms use disposable repositories,
real Git commits and actual application compile/unit-test subprocesses. Agent responses
and CAO terminal transport are simulated. They cover:

1. Context JSON repair, plan review/revision and reviewed-plan digest binding.
2. Synthetic test approval and rejection of a plan modified after approval.
3. Implementation verification failure followed by the existing single repair attempt.
4. Automatic remediation of an eligible finding while withholding protected findings.
5. Verification, re-review against the new HEAD and the human-review handoff.

An AST comparison against the baseline found **41 planning, 37 delivery and 35 source
review function bodies unchanged**, ignoring docstrings and Delivery's explicit
repair-policy argument. Differences were limited to the two intended shared-runtime
functions (generic headless error wording and parameterized repair instructions).
One unused source-review helper was omitted from its dependency closure.

## CAO validation and live run

All three generated bundles passed server-side validation using temporary candidates.
The installed `sdlc_dev_plan`, `sdlc_deliver` and `source_review` definitions and their
profiles were not replaced.

The isolated live test `modular-review-live-20260919-1` ran the bundled source workflow
as `sdlc_modular_review_test_20260919`. It completed all five agent stages without a
contract-repair turn. Runtime evidence remains Git-ignored under:

```text
.agentic-sdlc/runtime/source-review/modular-review-live-20260919-1/
```

The final result was `REVIEWED`, with the internal slice defect routed to `AUTO_FIX`
and the authorization defect routed to `HUMAN_REQUIRED`. Coverage remained explicitly
`INCOMPLETE` because of the fixture's limited application context.

The test used the versioned fixture generator, a disposable Git repository and the
existing reviewer profiles. No real PR was created, changed or commented on. The
additional final builder guard for package initializers/module-relative paths was
verified by regression tests and final CAO validation after this live run; workflow
logic and shared runtime were unchanged.

## Limits

Actual CAO execution was exercised with Source Review. Planning and Delivery received
full deterministic integration coverage and CAO validation, not fresh production
agent runs against Jira/GitHub. GitHub publishing and live Atlassian access were not
exercised against external accounts. Frozen-source portability was tested in isolated
Python with replayed answer files; no crashed production CAO run was resumed.
