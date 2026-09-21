# Configurable source roots: live verification (2026-09-21)

Result: Delivery and the write-scope hook work from the registry's `source_roots` against the
real CAO server and Claude Code. The hook was verified directly against a real CAO terminal; the
negative delivery run did not exercise the hook, because the agents stayed inside the roots.

## Setup

- The three changed profiles (`implementer`, `remediator`, `pr-reviewer`) and then the
  `sdlc_deliver` bundle were reinstalled from the working tree; every installed copy matched the
  repository.
- Runs used a throwaway clone under `$HOME` (CAO refuses `/tmp`) with no remote. Its working tree
  was the repository's uncommitted changes, and it held the originally human-approved PAY-DEMO-001
  plan records (plan hash, approval and baseline verified). The sample application was in its
  original "before" state.
- Each run: `cao workflow run sdlc_deliver --wait --json --input ticket_id=PAY-DEMO-001
  --input repository_root=<clone> --input base_branch=main --input implementation_mode=hybrid`.

## Check 1: two narrower roots (positive)

`source_roots: ["app/payment_service", "app/tests"]`, so `app/README.md` and everything else
under `app/` is outside the roots.

- The run ended `AWAITING_HUMAN_REVIEW`. Steps: dispatch, four workers, integration, PR review,
  one automatic remediation round, PR review again.
- Two commits: `Implement approved plan (hybrid)` and `Remediate findings (PR-003, PR-004)`.
  Together they touch exactly `app/payment_service/payment_service.py`,
  `app/payment_service/repository.py` and `app/tests/test_payment_service.py`. Nothing outside the
  roots changed.
- `delivery-manifest.json` records `source_roots: ["app/payment_service", "app/tests"]`.
  Verification (`compileall`, `unittest discover`) passed, and 7 tests pass when run independently
  on the delivered branch.
- The remediation round is the first live use of the multi-root `git add` path.
- The hook denied nothing: the workers stayed inside the roots.

## Check 2: the plan needs files outside the roots (negative delivery run)

`source_roots: ["app/tests"]` only. The approved plan requires production changes under
`app/payment_service/`, which are now outside the roots.

- Production code was **not** changed. The only commit touches `app/tests/test_payment_service.py`.
- The agents complied with the roots line in the prompt and the updated profile text. They wrote
  tests only and reported the production tasks as deviations ("the file lies outside the writable
  source root"). No hook denial appears in the terminal logs, so **this run did not exercise the hook**.
- The run ended `failed`: the verification-repair step changed nothing inside the roots and raised
  `implement-v1-repair-1 completed but left no changes under the source roots (app/tests)`. The message
  names the roots. See the observations below about `failed` versus `BLOCKED`.

## Check 3: the hook against a real CAO terminal

A real `sdlc_implementer` terminal was created through CAO's API, and the hook was run against its
terminal id, so the profile came from CAO's own metadata over HTTP, with the real registry file.
The terminal was deleted afterwards.

| Registry state | Path | Decision |
|---|---|---|
| `source_roots: ["app/tests"]` | `app/tests/test_new.py` | allow |
| | `.agentic-sdlc/runtime/PAY-DEMO-001/x.answer.json` (answer channel) | allow |
| | `app/payment_service/payment_service.py`, `app/README.md` | deny |
| | `app/tests/../payment_service/payment_service.py` (traversal) | deny |
| | `.agentic-sdlc/cao/specialists.json`, `agentic-sdlc-records/...`, `.git/config` | deny |
| edited to `["app/payment_service", "app/tests"]`, same terminal, no restart | `app/payment_service/payment_service.py`, `app/tests/test_new.py` | allow |
| | `app/README.md` | deny |
| registry replaced by malformed JSON | `app/tests/test_new.py`, `app/payment_service/payment_service.py` | deny (no fallback to `app/`) |
| | the answer-file path | allow |

The deny reason for the broken registry tells the agent that the source-root configuration is
invalid and that no source root is writable until it is fixed.

## Observations and limits

- **`failed` versus `BLOCKED`.** When the first hybrid implementation step fails, Delivery ends
  `BLOCKED` with a reason. When the verification-repair step (or a remediation) makes no change,
  it raises and the run ends `failed`, leaving the manifest at `IMPLEMENTED`. That is existing behaviour
  that Check 2 made visible; a plan that needs files outside the roots is now an easy way to reach it.
  Ending `BLOCKED` with a reason such as "the plan requires changes outside the source roots" would be a
  small follow-up.
- Compliance and enforcement are separate. Check 2 shows the agents follow the roots they are told;
  Check 3 shows the hook refuses anything else. Only Check 3 proves the boundary.
- Not exercised: a real multi-module project with its own build tool (the registry's verification suites
  still use the sample app's `app` commands), skills, and a specialist profile limited to part of a root.
  The planner does not yet know the roots.
