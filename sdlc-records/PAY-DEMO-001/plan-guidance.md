# Developer guidance: PAY-DEMO-001

> **Synthetic test guidance.** Written by the assistant on 2026-09-20 to exercise the warm-start
> live test (M5 of `docs/plans/planning-guidance-and-warm-start.md`). It is not a real human
> decision and must not be used to approve or deliver anything.

## Decisions

- **D1 (non-uniqueness integrity errors)** — `record_if_new` must return `False` only for a
  violation of the `provider_event_id` PRIMARY KEY. Any other `sqlite3.IntegrityError`
  (for example NOT NULL on `payment_id`) must propagate to the caller so it is treated as a
  non-duplicate persistence failure under FR-4, and must not be logged as a duplicate callback.
  The database constraint stays the sole arbiter: no SELECT-before-INSERT pre-check and no lock.

## Constraints

- The Python runtime is 3.10, where `sqlite3.Error.sqlite_errorcode` is not available. Distinguish
  the uniqueness violation by inspecting the error message for
  `UNIQUE constraint failed: processed_callbacks.provider_event_id`.
- No new runtime dependency and no schema migration.

## Answers to reviewer findings

- **r1:PLAN-001** — Decided by D1: this is a scoped change to `record_if_new`, with a test that a
  null `payment_id` raises instead of being reported as a duplicate. Correct the FR-4 traceability
  wording so it no longer says only non-`IntegrityError` failures are non-duplicates.
