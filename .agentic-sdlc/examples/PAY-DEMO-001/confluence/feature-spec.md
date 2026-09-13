# Payment Callback Processing

Source ID: CONF:4812

## Background

Each provider callback includes a provider event ID that is stable across retransmissions of the same event.

## Functional requirements

A provider event that has already completed business processing must not execute fulfilment again.

If processing fails before completion, the provider is allowed to retry the event.

## Compatibility

The external HTTP response contract for callbacks must remain unchanged by this feature.

## Observability

The service should record enough information to distinguish a duplicate callback from a first delivery in operational logs.
