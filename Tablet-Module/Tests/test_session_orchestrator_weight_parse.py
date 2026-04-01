import unittest
from datetime import datetime
from types import SimpleNamespace

from close_gates import CloseGates
from session_models import ControllerEvent, ControllerEventType, LockerSession, SessionPhase, ValidationResult
from session_orchestrator import SessionOrchestrator


class FakeFirebaseRepo:
    def __init__(self):
        self.updated_weight = None
        self.appended_events = []
        self.booking = {"userId": "user-1"}
        self.profile = {"email": "user@example.com", "displayName": "User One"}

    def update_booking_measured_weight(self, booking_id, measured_weight_grams):
        self.updated_weight = (booking_id, measured_weight_grams)

    def append_booking_event(self, booking_id, event_type, actor_uid, data):
        self.appended_events.append((booking_id, event_type, actor_uid, data))

    def get_booking(self, booking_id):
        return self.booking

    def get_profile(self, uid):
        return self.profile


class SessionOrchestratorWeightParseTests(unittest.TestCase):
    def _build_orchestrator(self):
        return SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=FakeFirebaseRepo(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
        )

    def test_parses_measured_weight_grams_alias(self):
        orchestrator = self._build_orchestrator()
        orchestrator._active_validation = ValidationResult(
            allowed=True,
            token_data={"purpose": "COURIER_DROP"},
        )
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            weight_expected_grams=1500,
        )

        called = {"capture": False}
        orchestrator._capture_signature_and_close_if_ready = lambda: called.__setitem__("capture", True)

        orchestrator._on_weight_measured(
            ControllerEvent(
                event_type=ControllerEventType.WEIGHT_MEASURED,
                locker_id="Locker 1",
                request_id="req-1",
                payload={"measuredWeightGrams": "1500"},
            )
        )

        self.assertEqual(orchestrator._active_session.weight_measured_grams, 1500)
        self.assertTrue(orchestrator._active_session.weight_accepted)
        self.assertEqual(orchestrator._firebase_repo.updated_weight, ("book-1", 1500))
        self.assertTrue(called["capture"])

    def test_courier_drop_close_ack_issues_pickup_token_and_email(self):
        repo = FakeFirebaseRepo()
        sent_email = {"called": False, "to": None}

        class FakeTokenService:
            def issue_user_pickup_token(self, booking_id):
                return SimpleNamespace(token_id="upk_123", purpose="USER_PICKUP")

        class FakeEmailNotifier:
            def send_token_email_async(self, **kwargs):
                sent_email["called"] = True
                sent_email["to"] = kwargs.get("to_email")

        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            token_service=FakeTokenService(),
            email_notifier=FakeEmailNotifier(),
        )

        orchestrator._issue_pickup_token_and_notify("book-1")
        self.assertTrue(sent_email["called"])
        self.assertEqual(sent_email["to"], "user@example.com")

    def test_user_pickup_close_path_does_not_mutate_frozen_gate_config(self):
        published = {"close": False}

        class FakeMQTT:
            def publish_close(self, **kwargs):
                published["close"] = True

        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=FakeMQTT(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=FakeFirebaseRepo(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),  # require_weight defaults to True
        )
        orchestrator._active_validation = ValidationResult(
            allowed=True,
            token_data={"purpose": "USER_PICKUP"},
        )
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.READY_TO_CLOSE,
            signature_captured_at=datetime.utcnow(),
            signature_path="/tmp/sign.png",
        )

        orchestrator._attempt_close_if_ready()
        self.assertTrue(published["close"])


if __name__ == "__main__":
    unittest.main()
