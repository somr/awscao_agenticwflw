# Payment Callback Processing

Source ID: CONF:4812

## Background

Each provider callback includes a provider event ID that is stable across retransmissions of the same event.

## Functional requirements

A provider event that has already completed business processing must not execute fulfilment again.

If processing fails before completion, the provider is allowed to retry the event.

If a duplicate callback for the same provider event ID arrives while the original delivery is still being processed (neither completed nor yet failed), the duplicate must not trigger a second fulfilment. Enforce this via a persistence-layer uniqueness constraint on the provider event ID rather than an application-level or distributed lock: the constraint check itself is the concurrency-safe idempotency guarantee, since a database rejects a concurrent duplicate insert independently of timing.

## Compatibility

The external HTTP response contract for callbacks must remain unchanged by this feature.

## Observability

The service should record enough information to distinguish a duplicate callback from a first delivery in operational logs.
