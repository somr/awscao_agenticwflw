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


if __name__ == "__main__":
    unittest.main()
