# PAY-DEMO-001 — Make payment callbacks idempotent

## Description

Payment providers can send the same callback more than once. A repeated provider event must not trigger duplicate payment processing or fulfilment.

## Acceptance Criteria

- **AC-1:** Repeated callbacks carrying the same provider event ID must not cause duplicate fulfilment.
- **AC-2:** Existing provider-facing HTTP response behaviour must remain backward compatible.
- **AC-3:** Duplicate callback attempts must be observable in application logs.

## Scope

The change applies to the payment callback processing path. Provider adapter public interfaces are out of scope.

## Documentation

- CONF:4812 — Payment Callback Processing
- CONF:4934 — Payment Service Architecture
