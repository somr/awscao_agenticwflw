# Payment Callback Processing

Source ID: CONF:4812

## Background

Each provider callback includes a provider event ID that is stable across retransmissions of the same event.

## Functional requirements

A provider event that has already completed business processing must not execute fulfilment again.

If processing fails before completion, the provider is allowed to retry the event. Specifically: "fails before completion" means fulfilment itself raises or otherwise fails after the event has been claimed (persisted under the uniqueness constraint). In that case the claim must be released (e.g. by deleting the claim row) so a subsequent retry with the same provider event ID can re-claim and attempt fulfilment again. A failure to claim in the first place (the persistence write itself failing for a reason other than a duplicate, e.g. a transient storage error) is not a retry scenario under this requirement — the provider's ordinary retry behaviour naturally results in the same claim attempt being repeated, with no released-claim bookkeeping needed.

If a duplicate callback for the same provider event ID arrives while the original delivery is still being processed (neither completed nor yet failed), the duplicate must not trigger a second fulfilment. Enforce this via a persistence-layer uniqueness constraint on the provider event ID rather than an application-level or distributed lock: the constraint check itself is the concurrency-safe idempotency guarantee, since a database rejects a concurrent duplicate insert independently of timing.

## Compatibility

The external HTTP response contract for callbacks must remain unchanged by this feature.

## Observability

The service should record enough information to distinguish a duplicate callback from a first delivery in operational logs.
