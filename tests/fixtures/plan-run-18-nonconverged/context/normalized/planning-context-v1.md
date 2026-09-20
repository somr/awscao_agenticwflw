# Planning Context — PAY-DEMO-001

**Summary:** Make payment callbacks idempotent

## Problem statement
Payment providers can send the same callback more than once. A repeated provider event must not trigger duplicate payment processing or fulfilment.

## Scope
### In scope
- The payment callback processing path.
- Continuing to use PaymentRepository's existing SQLite-backed implementation as-is, as an accepted stand-in for PostgreSQL for this repository's current baseline.

### Out of scope
- Provider adapter public interfaces.
- Introducing an actual PostgreSQL dependency.
- Automatic reconciliation or timeout recovery for events left permanently unresolved after a crash between callback acceptance and durable recording of its outcome; may be addressed by a follow-up ticket.
- Automatic recovery of a payment whose original in-flight delivery fails and is released for retry after a duplicate was already acknowledged with a successful response; may be addressed by a follow-up ticket (e.g. a reconciliation job over rows stuck in a non-final state).

### Uncertain
- None recorded.

## Acceptance criteria
- **AC-1** — Repeated callbacks carrying the same provider event ID must not cause duplicate fulfilment.  _[EXPLICIT; JIRA:PAY-DEMO-001:Acceptance Criteria, AC-1]_
- **AC-2** — Existing provider-facing HTTP response behaviour must remain backward compatible.  _[EXPLICIT; JIRA:PAY-DEMO-001:Acceptance Criteria, AC-2]_
- **AC-3** — Duplicate callback attempts must be observable in application logs.  _[EXPLICIT; JIRA:PAY-DEMO-001:Acceptance Criteria, AC-3]_

## Functional requirements
- **FR-1** — A provider event that has already completed business processing must not execute fulfilment again.  _[EXPLICIT; CONF:4812:Functional requirements, paragraph 1]_
- **FR-2** — If fulfilment itself raises or otherwise fails after the event has been claimed (persisted under the uniqueness constraint), the claim must be released (e.g. by deleting the claim row) so that a subsequent retry with the same provider event ID can re-claim and attempt fulfilment again.  _[EXPLICIT; CONF:4812:Functional requirements, paragraph 2]_
- **FR-3** — A failure to claim in the first place (the persistence write itself failing for a reason other than a duplicate, e.g. a transient storage error) is not a retry scenario requiring released-claim bookkeeping; the provider's ordinary retry behaviour results in the same claim attempt being repeated.  _[EXPLICIT; CONF:4812:Functional requirements, paragraph 2]_
- **FR-4** — If a duplicate callback for the same provider event ID arrives while the original delivery is still being processed (neither completed nor failed), the duplicate must not trigger a second fulfilment.  _[EXPLICIT; CONF:4812:Functional requirements, paragraph 3]_
- **FR-5** — A rejected duplicate that arrives while the original delivery is still in flight must receive the same successful response as any other duplicate, per the unchanged response contract.  _[EXPLICIT; CONF:4934:Constraints, paragraph on rejected duplicate arriving while original delivery is in flight]_
- **FR-6** — The service must record enough information in operational logs to distinguish a duplicate callback from a first delivery.  _[NORMALIZED_FROM_SOURCE; CONF:4812:Observability, JIRA:PAY-DEMO-001:Acceptance Criteria, AC-3]_
- **FR-7** — The external HTTP response contract for callbacks must remain unchanged by this feature.  _[EXPLICIT; CONF:4812:Compatibility, JIRA:PAY-DEMO-001:Acceptance Criteria, AC-2]_

## Non-functional requirements
- **NFR-1** — Idempotency for concurrent duplicate deliveries must be enforced by a persistence-layer uniqueness constraint on the provider event ID, which rejects a concurrent duplicate insert independently of timing, rather than by an application-level or distributed lock.  _[EXPLICIT; CONF:4812:Functional requirements, paragraph 3]_
- **NFR-2** — Concurrent callback requests are each handled in their own thread/request context, and PaymentRepository must be constructed per-request (a fresh connection to the same underlying persistence store) rather than shared as a single long-lived instance across concurrent requests, so that concurrent inserts race at the persistence layer under the uniqueness constraint.  _[EXPLICIT; CONF:4934:Transaction model, paragraph 2]_
- **NFR-3** — Operational logs must allow a duplicate callback to be distinguished from a first delivery.  _[NORMALIZED_FROM_SOURCE; CONF:4812:Observability, JIRA:PAY-DEMO-001:Acceptance Criteria, AC-3]_

## Constraints
- **CON-1** — Do not introduce a distributed lock service solely for callback idempotency.  _[EXPLICIT; CONF:4934:Constraints, paragraph 1]_
- **CON-2** — Reuse existing persistence and transaction infrastructure where practical.  _[EXPLICIT; CONF:4934:Constraints, paragraph 2]_
- **CON-3** — Every idempotency-relevant write, including a retried event after a prior failure, must go through the same persistence-layer uniqueness constraint on the provider event ID (e.g. delete-then-reinsert under that constraint), not a separate mutable-status compare-and-swap (such as a conditional UPDATE ... WHERE status = 'failed'). A status-based compare-and-swap does not satisfy this constraint.  _[EXPLICIT; CONF:4934:Constraints, paragraph 3]_
- **CON-4** — A crash after a callback is accepted but before its outcome (success or failure) is durably recorded, leaving the event permanently unresolved with no automatic recovery, is a known limitation accepted for this ticket's scope.  _[EXPLICIT; CONF:4934:Constraints, paragraph 4]_
- **CON-5** — If the original claimant's processing fails and is released for retry after a duplicate was rejected with a successful acknowledgement, no further callback for that provider event ID may arrive; automatic recovery of that payment is a known limitation accepted for this ticket's scope.  _[EXPLICIT; CONF:4934:Constraints, paragraph 5]_
- **CON-6** — PaymentRepository's existing SQLite-backed implementation is the accepted stand-in for PostgreSQL (the system of record) for this repository's current baseline; it is in scope to keep using as-is, and introducing an actual PostgreSQL dependency is out of scope.  _[EXPLICIT; CONF:4934:Persistence]_
- **CON-7** — Provider adapter public interfaces are out of scope for this change.  _[EXPLICIT; JIRA:PAY-DEMO-001:Scope]_

## Open questions
- **OQ-1** (non-blocking) — What is the exact existing provider-facing HTTP response (status code and body) for a callback, and in particular for a duplicate callback, that must be preserved?
- **OQ-2** (non-blocking) — What specific information, log level and format must a duplicate-callback log entry contain?
- **OQ-3** (non-blocking) — Does the existing SQLite-backed PaymentRepository schema already provide a uniqueness constraint on the provider event ID, or is adding one within the scope of 'keep using as-is'?
- **OQ-4** (non-blocking) — What state or status must the claim record carry to distinguish completed, in-flight and failed events, and what behaviour is expected when a duplicate arrives for an event already completed versus one in flight?

## Contradictions
- None.

## Sources
- `JIRA:PAY-DEMO-001` — Make payment callbacks idempotent (JIRA, RETRIEVED)
- `CONF:4812` — Payment Callback Processing (CONFLUENCE, RETRIEVED)
- `CONF:4934` — Payment Service Architecture (CONFLUENCE, RETRIEVED)
