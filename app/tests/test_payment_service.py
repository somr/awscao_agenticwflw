import hashlib
import hmac
import json
import logging
import tempfile
import unittest
from pathlib import Path

from payment_service.callback_controller import CallbackController
from payment_service.fulfilment_service import FulfilmentService
from payment_service.payment_service import PaymentService
from payment_service.repository import PaymentRepository


class PaymentServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "payments.db"
        self.repository = PaymentRepository(db_path)
        self.fulfilment_service = FulfilmentService()
        self.payment_service = PaymentService(self.repository, self.fulfilment_service)
        self.controller = CallbackController(self.payment_service)

    def tearDown(self):
        self.repository.close()
        self._tmpdir.cleanup()

    def test_first_callback_fulfils_once(self):
        response = self.controller.handle({"provider_event_id": "evt-1", "payment_id": "pay-1"})
        self.assertEqual(response, {"status": "ok", "payment_id": "pay-1"})
        self.assertEqual(self.fulfilment_service.fulfilment_count("pay-1"), 1)

    def test_duplicate_callback_does_not_refulfil(self):
        self.controller.handle({"provider_event_id": "evt-1", "payment_id": "pay-1"})
        self.controller.handle({"provider_event_id": "evt-1", "payment_id": "pay-1"})
        self.assertEqual(
            self.fulfilment_service.fulfilment_count("pay-1"),
            1,
            "a duplicate provider_event_id must not trigger fulfilment a second time",
        )

    def test_response_shape_is_identical_for_new_and_duplicate(self):
        first = self.controller.handle({"provider_event_id": "evt-2", "payment_id": "pay-2"})
        second = self.controller.handle({"provider_event_id": "evt-2", "payment_id": "pay-2"})
        self.assertEqual(set(first.keys()), set(second.keys()))
        self.assertEqual(first, second)

    def test_duplicate_is_distinguishable_in_logs(self):
        self.controller.handle({"provider_event_id": "evt-3", "payment_id": "pay-3"})
        with self.assertLogs("payment_service.payment_service", level="INFO") as captured:
            self.controller.handle({"provider_event_id": "evt-3", "payment_id": "pay-3"})
        joined = "\n".join(captured.output)
        self.assertIn("duplicate", joined.lower())

    def test_signed_callback_is_accepted(self):
        secret = b"test-secret"
        controller = CallbackController(self.payment_service, webhook_secret=secret)
        body = {"provider_event_id": "evt-4", "payment_id": "pay-4"}
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        self.assertEqual(controller.handle(body, signature), {"status": "ok", "payment_id": "pay-4"})

    def test_wrong_signature_is_rejected(self):
        controller = CallbackController(self.payment_service, webhook_secret=b"test-secret")
        with self.assertRaises(PermissionError):
            controller.handle({"provider_event_id": "evt-5", "payment_id": "pay-5"}, "0" * 64)
        self.assertEqual(self.fulfilment_service.fulfilment_count("pay-5"), 0)

    def test_recent_fulfilments_short_ledger(self):
        self.fulfilment_service.fulfil("pay-6")
        self.assertEqual(self.fulfilment_service._recent_fulfilments(3), ["pay-6"])


if __name__ == "__main__":
    unittest.main()
