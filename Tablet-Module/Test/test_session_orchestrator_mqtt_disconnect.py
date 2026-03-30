import unittest
from types import SimpleNamespace

from close_gates import CloseGates
from session_models import ValidationResult
from session_orchestrator import SessionOrchestrator


class _Repo:
    def create_unlock_request(self, **kwargs):
        return "req"

    def create_unlock_grant(self, **kwargs):
        return None


class _Scanner:
    def __init__(self):
        self._done = False

    def get_scan_nowait(self):
        if self._done:
            return None
        self._done = True
        return SimpleNamespace(normalized_text="tok-1")


class _Validator:
    def validate(self, *, token_id: str):
        return ValidationResult(
            allowed=True,
            token_data={"bookingId": "book-1", "lockerId": "Locker 1", "sectorId": "S1"},
            booking_data={},
        )


class _MQTT:
    def publish_open(self, **kwargs):
        raise RuntimeError("MQTT client not connected")


class _UI:
    def __init__(self):
        self.errors = []

    def show_validating(self, **kwargs):
        return None

    def show_unlocking(self, **kwargs):
        return None

    def show_error(self, *, reason: str):
        self.errors.append(reason)

    def show_idle(self):
        return None


class SessionOrchestratorMQTTDisconnectTests(unittest.TestCase):
    def test_scan_does_not_crash_when_open_publish_fails(self):
        ui = _UI()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=_Scanner(),
            access_validator=_Validator(),
            mqtt_client=_MQTT(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=_Repo(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
        )

        did_work = orchestrator._process_next_scan()

        self.assertTrue(did_work)
        self.assertIsNone(orchestrator._active_session)
        self.assertTrue(ui.errors)


class _RepoCloseAckFailure:
    def update_locker_state_post_session(self, **kwargs):
        raise RuntimeError("No data supplied")

    def update_booking_status_post_session(self, **kwargs):
        return None


class _UIWithCompleted(_UI):
    def __init__(self):
        super().__init__()
        self.completed = False

    def show_completed(self, **kwargs):
        self.completed = True


class SessionOrchestratorCloseAckFailureTests(unittest.TestCase):
    def test_close_ack_does_not_crash_when_firebase_post_session_update_fails(self):
        ui = _UIWithCompleted()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=SimpleNamespace(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=_RepoCloseAckFailure(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
        )
        orchestrator._active_validation = ValidationResult(
            allowed=True,
            token_data={"purpose": "USER_PICKUP"},
        )
        orchestrator._active_session = SimpleNamespace(
            sector_id="S1",
            locker_id="Locker 1",
            booking_id="book-1",
        )

        orchestrator._on_close_ack(SimpleNamespace())

        self.assertIsNone(orchestrator._active_session)
        self.assertTrue(ui.errors)
        self.assertFalse(ui.completed)


if __name__ == "__main__":
    unittest.main()
