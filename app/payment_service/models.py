"""Data shapes for the payment callback idempotency service."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallbackEvent:
    provider_event_id: str
    payment_id: str


@dataclass(frozen=True)
class ProcessingResult:
    payment_id: str
    fulfilled: bool
