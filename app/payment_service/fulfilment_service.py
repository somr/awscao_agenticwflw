"""Fulfilment side effect for a processed payment callback."""
from __future__ import annotations


class FulfilmentService:
    """Stands in for whatever downstream fulfilment action a real payment
    callback would trigger. Keeps an in-memory ledger so tests can assert
    exactly how many times fulfilment ran for a given payment."""

    def __init__(self) -> None:
        self.ledger: list[str] = []

    def fulfil(self, payment_id: str) -> None:
        self.ledger.append(payment_id)

    def fulfilment_count(self, payment_id: str) -> int:
        return self.ledger.count(payment_id)

    def _recent_fulfilments(self, limit: int) -> list[str]:
        """Internal helper: the newest ``limit`` fulfilled payment IDs, newest
        first. ``limit`` is non-negative; only used for diagnostics logging."""
        return list(reversed(self.ledger))[: limit + 1]
