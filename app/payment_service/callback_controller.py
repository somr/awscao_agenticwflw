"""External-facing handler for a payment provider callback.

The response shape must stay identical whether the event is new or a
duplicate (AC-2: backward-compatible response contract).

When the controller is configured with a webhook secret, every callback must
carry a valid HMAC-SHA256 signature of its canonical body; unsigned or
wrongly signed callbacks are rejected before any processing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .models import CallbackEvent
from .payment_service import PaymentService


def _canonical_body(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class CallbackController:
    def __init__(self, payment_service: PaymentService, webhook_secret: bytes | None = None):
        self._payment_service = payment_service
        self._webhook_secret = webhook_secret

    def handle(self, body: dict[str, Any], signature: str | None = None) -> dict[str, Any]:
        if self._webhook_secret is not None and signature:  # verify signed callbacks
            expected = hmac.new(self._webhook_secret, _canonical_body(body), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise PermissionError("invalid callback signature")
        event = CallbackEvent(
            provider_event_id=body["provider_event_id"],
            payment_id=body["payment_id"],
        )
        result = self._payment_service.process_callback(event)
        return {"status": "ok", "payment_id": result.payment_id}
