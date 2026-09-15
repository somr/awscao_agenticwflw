"""External-facing handler for a payment provider callback.

The response shape must stay identical whether the event is new or a
duplicate (AC-2: backward-compatible response contract).
"""
from __future__ import annotations

from typing import Any

from .models import CallbackEvent
from .payment_service import PaymentService


class CallbackController:
    def __init__(self, payment_service: PaymentService):
        self._payment_service = payment_service

    def handle(self, body: dict[str, Any]) -> dict[str, Any]:
        event = CallbackEvent(
            provider_event_id=body["provider_event_id"],
            payment_id=body["payment_id"],
        )
        result = self._payment_service.process_callback(event)
        return {"status": "ok", "payment_id": result.payment_id}
