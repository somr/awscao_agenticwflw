# Payment Service Architecture

Source ID: CONF:4934

## Relevant components

- CallbackController
- PaymentService
- PaymentRepository
- FulfilmentService

## Persistence

PostgreSQL is the system of record for payment state.

## Transaction model

Payment state transitions and fulfilment coordination are initiated by PaymentService.

## Constraints

Do not introduce a distributed lock service solely for callback idempotency.

Reuse existing persistence and transaction infrastructure where practical.
