import unittest
from datetime import datetime
from types import SimpleNamespace

from close_gates import CloseGates
from session_models import ControllerEvent, ControllerEventType, LockerSession, SessionPhase, ValidationResult
from session_orchestrator import SessionOrchestrator




class FakeMqttClose:
    def __init__(self):
        self.closed = []

    def publish_close(self, **kwargs):
        self.closed.append(kwargs)


class FakeFirebaseRepo:
    def __init__(self):
        self.updated_weight = None
        self.appended_events = []
        self.booking = {"userId": "user-1"}
        self.profile = {"email": "user@example.com", "displayName": "User One"}
        self.marked_token = None
        self.mark_qr_token_used_calls = 0
        self.heartbeat_updates = []
        self.tamper_updates = []

    def update_booking_measured_weight(self, booking_id, measured_weight_grams):
        self.updated_weight = (booking_id, measured_weight_grams)

    def append_booking_event(self, booking_id, event_type, actor_uid, data):
        self.appended_events.append((booking_id, event_type, actor_uid, data))

    def get_booking(self, booking_id):
        return self.booking

    def get_profile(self, uid):
        return self.profile

    def mark_qr_token_used(self, token_id):
        self.marked_token = token_id
        self.mark_qr_token_used_calls += 1

    def update_locker_state_post_session(self, **kwargs):
        return None

    def update_booking_status_post_session(self, **kwargs):
        return None

    def update_locker_heartbeat(self, **kwargs):
        self.heartbeat_updates.append(kwargs)

    def update_locker_tamper(self, **kwargs):
        self.tamper_updates.append(kwargs)

    def get_json(self, path):
        if path == "sectors/S1":
            return {"adminUids": {"admin-1": True}}
        if path == "profiles":
            return {
                "admin-1": {
                    "role": "admin",
                    "sectorId": "S1",
                    "email": "user@example.com",
                    "displayName": "User One",
                },
                "super-1": {
                    "role": "superAdmin",
                    "sectorId": None,
                    "email": "super@example.com",
                    "displayName": "Super Admin",
                }
            }
        return {}


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
            phase=SessionPhase.WAITING_FOR_OTHER_GATES,
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

    def test_signature_cancel_clears_session_without_marking_token_used(self):
        repo = FakeFirebaseRepo()

        class FakeUI:
            def __init__(self):
                self.cancelled = False
                self.idle_called = False

            def show_operation_cancelled(self):
                self.cancelled = True

            def show_idle(self):
                self.idle_called = True

        class FakeSignatureCapture:
            def capture_signature(self, **kwargs):
                return SimpleNamespace(
                    captured=False,
                    valid=False,
                    validation_reason="SIGNATURE_CAPTURE_CANCELLED",
                    signed_at=None,
                    local_file_path=None,
                )

        ui = FakeUI()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=FakeMqttClose(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=FakeSignatureCapture(),
            close_gates=CloseGates(),
            ui_controller=ui,
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
            phase=SessionPhase.WAITING_FOR_SIGNATURE,
        )

        orchestrator._capture_signature_and_close_if_ready()

        self.assertIsNotNone(orchestrator._active_session)
        self.assertEqual(orchestrator._active_session.phase, SessionPhase.CLOSING)
        self.assertEqual(len(orchestrator._mqtt_client.closed), 1)
        self.assertIsNone(repo.marked_token)
        self.assertTrue(ui.cancelled)
        self.assertFalse(ui.idle_called)

    def test_cancel_request_sends_close_and_waits_for_ack(self):
        repo = FakeFirebaseRepo()

        class FakeUI:
            def __init__(self):
                self.cancelled = False
                self.cancel_requests = [True]

            def get_cancel_nowait(self):
                return self.cancel_requests.pop(0) if self.cancel_requests else False

            def show_operation_cancelled(self):
                self.cancelled = True

        ui = FakeUI()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=FakeMqttClose(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
        )
        orchestrator._active_validation = ValidationResult(allowed=True, token_data={"purpose": "USER_PICKUP"})
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.WAITING_FOR_OTHER_GATES,
        )

        did_work = orchestrator._process_cancel_request()

        self.assertTrue(did_work)
        self.assertIsNotNone(orchestrator._active_session)
        self.assertEqual(orchestrator._active_session.phase, SessionPhase.CLOSING)
        self.assertEqual(len(orchestrator._mqtt_client.closed), 1)
        self.assertEqual(orchestrator._mqtt_client.closed[0]["request_id"], "req-1")
        self.assertTrue(ui.cancelled)
        self.assertEqual(repo.appended_events[-1][1], "SESSION_CANCELLED")

    def test_cancel_close_ack_clears_session_without_marking_token_used(self):
        repo = FakeFirebaseRepo()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=FakeMqttClose(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
        )
        orchestrator._active_validation = ValidationResult(allowed=True, token_data={"purpose": "USER_PICKUP"})
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.WAITING_FOR_SIGNATURE,
        )

        self.assertTrue(orchestrator._cancel_active_session(source="TABLET_BUTTON"))
        orchestrator._on_close_ack(
            ControllerEvent(
                event_type=ControllerEventType.CLOSE_ACK,
                locker_id="Locker 1",
                request_id="req-1",
            )
        )

        self.assertIsNone(orchestrator._active_session)
        self.assertIsNone(repo.marked_token)
        self.assertEqual(repo.mark_qr_token_used_calls, 0)
        self.assertEqual(repo.appended_events[-1][1], "SESSION_CANCEL_CLOSE_ACK")

    def test_cancel_request_is_rejected_while_unlocking(self):
        repo = FakeFirebaseRepo()

        class FakeUI:
            def __init__(self):
                self.cancelled = False
                self.cancel_requests = [True]
                self.errors = []

            def get_cancel_nowait(self):
                return self.cancel_requests.pop(0) if self.cancel_requests else False

            def show_operation_cancelled(self):
                self.cancelled = True

            def show_error(self, reason):
                self.errors.append(reason)

        ui = FakeUI()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
        )
        orchestrator._active_validation = ValidationResult(allowed=True, token_data={"purpose": "USER_PICKUP"})
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.UNLOCKING,
        )

        did_work = orchestrator._process_cancel_request()

        self.assertTrue(did_work)
        self.assertIsNotNone(orchestrator._active_session)
        self.assertFalse(ui.cancelled)
        self.assertEqual(repo.appended_events, [])
        self.assertEqual(len(ui.errors), 1)

    def test_close_ack_marks_qr_token_used_after_session_completion(self):
        repo = FakeFirebaseRepo()
        completed = {"called": False}

        class FakeUI:
            def show_completed(self, **kwargs):
                completed["called"] = True

            def show_idle(self):
                return None

        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=FakeUI(),
        )
        orchestrator._active_validation = ValidationResult(allowed=True, token_data={"purpose": "USER_PICKUP"})
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.CLOSING,
            signature_captured_at=datetime.utcnow(),
            signature_path="/tmp/signature.png",
        )

        orchestrator._on_close_ack(
            ControllerEvent(
                event_type=ControllerEventType.CLOSE_ACK,
                locker_id="Locker 1",
                request_id="req-1",
            )
        )

        self.assertEqual(repo.marked_token, "tok-1")
        self.assertEqual(repo.mark_qr_token_used_calls, 1)
        self.assertIsNone(orchestrator._active_session)
        self.assertTrue(completed["called"])

    def test_close_ack_timeout_marks_token_used_before_failing(self):
        repo = FakeFirebaseRepo()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
        )
        orchestrator._active_validation = ValidationResult(allowed=True, token_data={"purpose": "USER_PICKUP"})
        orchestrator._active_session = LockerSession(
            request_id="req-1",
            token_id="tok-1",
            booking_id="book-1",
            locker_id="Locker 1",
            sector_id="S1",
            device_uid="dev-1",
            phase=SessionPhase.CLOSING,
        )
        orchestrator._active_close_sent_mono = 0.0
        orchestrator._close_ack_timeout_s = 0.0

        did_work = orchestrator._check_session_timeouts()

        self.assertTrue(did_work)
        self.assertEqual(repo.marked_token, "tok-1")
        self.assertIsNone(orchestrator._active_session)

    def test_tamper_heartbeat_updates_tamper_and_notifies_admin_once(self):
        repo = FakeFirebaseRepo()
        sent = []

        class FakeEmailNotifier:
            def send_tamper_alert_email_async(self, **kwargs):
                sent.append(kwargs)

        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            email_notifier=FakeEmailNotifier(),
        )

        event = ControllerEvent(
            event_type=ControllerEventType.HEARTBEAT,
            locker_id="Locker 1",
            payload={"ts": 1000, "tamper": True},
        )
        orchestrator._handle_background(event)
        orchestrator._handle_background(event)

        self.assertEqual(len(repo.heartbeat_updates), 2)
        self.assertEqual(len(repo.tamper_updates), 2)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to_email"], "user@example.com")

    def test_tamper_email_falls_back_to_superadmin_when_sector_mapping_missing(self):
        repo = FakeFirebaseRepo()
        sent = []

        def _get_json(path):
            if path == "sectors/S1":
                return {}
            if path == "profiles":
                return {
                    "admin-2": {
                        "role": "admin",
                        "sectorId": "S1",
                        "email": "admin2@example.com",
                        "displayName": "Admin Two",
                    },
                    "super-1": {
                        "role": "superAdmin",
                        "sectorId": None,
                        "email": "super@example.com",
                        "displayName": "Super Admin",
                    }
                }
            return {}

        repo.get_json = _get_json

        class FakeEmailNotifier:
            def send_tamper_alert_email_async(self, **kwargs):
                sent.append(kwargs)

        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            email_notifier=FakeEmailNotifier(),
        )

        orchestrator._handle_background(
            ControllerEvent(
                event_type=ControllerEventType.HEARTBEAT,
                locker_id="Locker 1",
                payload={"ts": 1000, "tamper": "true"},
            )
        )
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to_email"], "super@example.com")


if __name__ == "__main__":
    unittest.main()
