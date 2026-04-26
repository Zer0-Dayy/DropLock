import unittest
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time

from close_gates import CloseGates
from datetime import datetime

from session_models import LockerSession, SessionPhase, ValidationResult
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

    def show_closing(self, **kwargs):
        return None

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
    def mark_qr_token_used(self, **kwargs):
        return None

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
            token_id="tok-1",
        )

        orchestrator._on_close_ack(SimpleNamespace())

        self.assertIsNone(orchestrator._active_session)
        self.assertTrue(ui.errors)
        self.assertFalse(ui.completed)


class _MQTTCloseFail:
    def is_connected(self):
        return True

    def publish_close(self, **kwargs):
        raise RuntimeError("MQTT client not connected")


class SessionOrchestratorClosePublishFailureTests(unittest.TestCase):
    def test_attempt_close_if_ready_handles_publish_failure_without_crashing(self):
        ui = _UIWithCompleted()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=_MQTTCloseFail(),
            controller_event_parser=SimpleNamespace(),
            firebase_repo=SimpleNamespace(),
            signature_capture=SimpleNamespace(),
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
            phase=SessionPhase.READY_TO_CLOSE,
            signature_path="/tmp/sign.png",
            signature_captured_at=datetime.utcnow(),
        )

        orchestrator._attempt_close_if_ready()

        self.assertIsNotNone(orchestrator._active_session)
        self.assertEqual(orchestrator._active_session.phase, SessionPhase.READY_TO_CLOSE)
        self.assertTrue(ui.errors)


class _MQTTRetryOnce:
    def __init__(self):
        self.starts = 0

    def is_running(self):
        return False

    def start(self):
        self.starts += 1
        if self.starts == 1:
            raise RuntimeError("timeout")


class _UIConnect(_UI):
    def __init__(self):
        super().__init__()
        self.establishing_count = 0
        self.idle_count = 0

    def show_establishing_connection(self):
        self.establishing_count += 1

    def show_idle(self):
        self.idle_count += 1


class SessionOrchestratorMQTTRetryTests(unittest.TestCase):
    def test_start_retries_mqtt_until_connected(self):
        ui = _UIConnect()
        mqtt = _MQTTRetryOnce()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(start=lambda: None),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=SimpleNamespace(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
        )

        with patch("session_orchestrator.time.sleep", return_value=None):
            orchestrator.start()

        self.assertEqual(mqtt.starts, 2)
        self.assertEqual(ui.establishing_count, 1)
        self.assertEqual(ui.idle_count, 1)


class _MQTTAlwaysFail:
    def __init__(self):
        self.starts = 0

    def is_running(self):
        return False

    def start(self):
        self.starts += 1
        raise TimeoutError("timed out")


class SessionOrchestratorMQTTRetryLimitTests(unittest.TestCase):
    def test_start_stops_retrying_after_max_attempts_and_surfaces_error(self):
        ui = _UIConnect()
        mqtt = _MQTTAlwaysFail()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(start=lambda: None),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=SimpleNamespace(),
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            ui_controller=ui,
            mqtt_start_retry_delay_s=0.01,
            mqtt_start_max_attempts=3,
        )

        with patch("session_orchestrator.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                orchestrator.start()

        self.assertEqual(mqtt.starts, 3)
        self.assertEqual(ui.establishing_count, 2)
        self.assertTrue(ui.errors)


class _RepoAdminCommands:
    def __init__(self, *, booking_status="COMPLETED", fail_delete_attempts=0):
        self.booking_status = booking_status
        self.fail_delete_attempts = fail_delete_attempts
        self.deleted = []
        self.reads = 0
        self.commands = {
            "L3": {
                "cmd123": {"cmd": "OPEN", "actorUid": "admin-1"},
            }
        }

    def get_admin_commands(self, sector_id: str):
        self.reads += 1
        return self.commands

    def get_locker(self, sector_id: str, locker_id: str):
        return {"activeBookingId": "b-1"}

    def get_booking(self, booking_id: str):
        return {"status": self.booking_status}

    def delete_admin_command(self, *, sector_id: str, locker_id: str, cmd_id: str):
        if self.fail_delete_attempts > 0:
            self.fail_delete_attempts -= 1
            raise RuntimeError("delete failed")
        self.deleted.append((sector_id, locker_id, cmd_id))
        locker_cmds = self.commands.get(locker_id) or {}
        locker_cmds.pop(cmd_id, None)


class _MQTTAdmin:
    def __init__(self):
        self.published = []
        self.gate = threading.Event()

    def publish_json(self, *, topic: str, payload: dict):
        self.gate.wait(timeout=1.0)
        self.published.append((topic, payload))


class SessionOrchestratorAdminOpenTests(unittest.TestCase):
    def test_admin_open_is_dispatched_and_acknowledged(self):
        repo = _RepoAdminCommands(booking_status="COMPLETED")
        mqtt = _MQTTAdmin()
        mqtt.gate.set()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            admin_command_poll_interval_s=0.1,
        )

        orchestrator._execute_admin_open(locker_id="L3", cmd_id="cmd123", payload={"actorUid": "admin-1"})

        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(mqtt.published[0][0], "droplock/S1/L3/cmd")
        self.assertEqual(mqtt.published[0][1]["bookingId"], "b-1")
        self.assertEqual(mqtt.published[0][1]["tokenId"], "admin_cmd123")
        self.assertEqual(repo.deleted, [("S1", "L3", "cmd123")])

    def test_admin_open_is_still_dispatched_when_booking_is_pending(self):
        repo = _RepoAdminCommands(booking_status="PICKUP_PENDING")
        mqtt = _MQTTAdmin()
        mqtt.gate.set()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
        )

        orchestrator._execute_admin_open(locker_id="L3", cmd_id="cmd123", payload={"actorUid": "admin-1"})

        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(mqtt.published[0][0], "droplock/S1/L3/cmd")
        self.assertEqual(repo.deleted, [("S1", "L3", "cmd123")])

    def test_admin_commands_are_ignored_while_qr_flow_is_active(self):
        repo = _RepoAdminCommands(booking_status="COMPLETED")
        mqtt = _MQTTAdmin()
        mqtt.gate.set()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
        )
        orchestrator._active_session = SimpleNamespace()
        orchestrator._admin_command_last_poll_mono = 0.0

        did_work = orchestrator._process_next_admin_command()

        self.assertFalse(did_work)
        self.assertEqual(repo.reads, 0)
        self.assertEqual(mqtt.published, [])
        self.assertEqual(repo.deleted, [])

    def test_admin_command_processing_is_non_blocking(self):
        repo = _RepoAdminCommands(booking_status="COMPLETED")
        mqtt = _MQTTAdmin()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            admin_command_poll_interval_s=0.1,
        )
        orchestrator._admin_command_last_poll_mono = 0.0

        did_work = orchestrator._process_next_admin_command()

        self.assertTrue(did_work)
        self.assertEqual(repo.reads, 1)
        self.assertEqual(len(mqtt.published), 0)
        mqtt.gate.set()

    def test_admin_open_does_not_republish_when_delete_fails_once(self):
        repo = _RepoAdminCommands(booking_status="COMPLETED", fail_delete_attempts=1)
        mqtt = _MQTTAdmin()
        mqtt.gate.set()
        orchestrator = SessionOrchestrator(
            device_context=SimpleNamespace(device_uid="dev-1", sector_id="S1"),
            scanner_input=SimpleNamespace(),
            access_validator=SimpleNamespace(),
            mqtt_client=mqtt,
            controller_event_parser=SimpleNamespace(),
            firebase_repo=repo,
            signature_capture=SimpleNamespace(),
            close_gates=CloseGates(),
            admin_command_poll_interval_s=0.1,
        )
        orchestrator._admin_command_last_poll_mono = 0.0

        first_did_work = orchestrator._process_next_admin_command()
        for _ in range(10):
            if ("L3", "cmd123") in orchestrator._admin_open_pending_ack and "L3" not in orchestrator._admin_open_in_flight:
                break
            time.sleep(0.01)
        orchestrator._admin_command_last_poll_mono = 0.0
        second_did_work = orchestrator._process_next_admin_command()

        self.assertTrue(first_did_work)
        self.assertTrue(second_did_work)
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(repo.deleted, [("S1", "L3", "cmd123")])


if __name__ == "__main__":
    unittest.main()
