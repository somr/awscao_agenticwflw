"""Orchestrates persistence + fulfilment for a payment provider callback.

Known gap (tracked by PAY-DEMO-001): a duplicate callback for the same
provider_event_id currently triggers fulfilment again instead of being
recognized as already processed.
"""
from __future__ import annotations

import logging

from .fulfilment_service import FulfilmentService
from .models import CallbackEvent, ProcessingResult
from .repository import PaymentRepository

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, repository: PaymentRepository, fulfilment_service: FulfilmentService):
        self._repository = repository
        self._fulfilment_service = fulfilment_service

    def process_callback(self, event: CallbackEvent) -> ProcessingResult:
        self._repository.record_if_new(event.provider_event_id, event.payment_id)
        self._fulfilment_service.fulfil(event.payment_id)
        logger.info("callback processed for provider_event_id=%s", event.provider_event_id)
        return ProcessingResult(payment_id=event.payment_id, fulfilled=True)
