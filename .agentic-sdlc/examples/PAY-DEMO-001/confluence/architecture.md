# Payment Service Architecture

Source ID: CONF:4934

## Relevant components

- CallbackController
- PaymentService
- PaymentRepository
- FulfilmentService

## Persistence

PostgreSQL is the system of record for payment state. For this repository's current baseline, PaymentRepository's existing SQLite-backed implementation is an accepted stand-in for PostgreSQL and is in scope to keep using as-is; introducing an actual PostgreSQL dependency is out of scope for this ticket.

## Transaction model

Payment state transitions and fulfilment coordination are initiated by PaymentService.

## Constraints

Do not introduce a distributed lock service solely for callback idempotency.

Reuse existing persistence and transaction infrastructure where practical.

Every idempotency-relevant write — including a retried event after a prior failure — must go through the same persistence-layer uniqueness constraint on the provider event ID (e.g. delete-then-reinsert under that constraint), not a separate mutable-status compare-and-swap (such as a conditional `UPDATE ... WHERE status = 'failed'`). A status-based compare-and-swap is a different mechanism from a uniqueness constraint and does not satisfy this constraint even though it avoids an explicit lock.

A crash after a callback is accepted but before its outcome (success or failure) is durably recorded, leaving that event permanently unresolved with no automatic recovery, is a known limitation accepted for this ticket's scope. Automatic reconciliation/timeout recovery for that case is explicitly out of scope and may be addressed by a follow-up ticket.
