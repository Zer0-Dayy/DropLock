import unittest
from types import SimpleNamespace

from close_gates import CloseGates
from firebase_repo import FirebaseRepo
from session_orchestrator import SessionOrchestrator


class FirebaseRepoAdminCommandLoggingTests(unittest.TestCase):
    def test_log_admin_command_execution_writes_expected_path_and_shape(self):
        writes = []

        class RepoWithSpy(FirebaseRepo):
            def put_json(self, path, data):
                writes.append((path, data))
                return data

        repo = RepoWithSpy(id_token="id-token", db_url="https://example.firebaseio.com")
        repo.log_admin_command_execution(
            sector_id="S1",
            locker_id="L1",
            cmd_id="cmd-1",
            status="EXECUTED",
            command_payload={"cmd": "OPEN", "actorUid": "admin-1", "ts": 1710000000000},
            actor_uid="admin-1",
            booking_id="book-1",
            token_id="tok-1",
            open_request_id="admin_cmd-1",
            close_request_id="admin_cmd-1_close",
            close_dispatched=True,
            acked=True,
        )

        self.assertEqual(len(writes), 1)
        path, payload = writes[0]
        self.assertEqual(path, "adminCommandLogs/S1/L1/cmd-1")
        self.assertEqual(payload["status"], "EXECUTED")
        self.assertEqual(payload["cmd"], "OPEN")
        self.assertEqual(payload["actorUid"], "admin-1")
        self.assertTrue(payload["acked"])
        self.assertTrue(payload["closeDispatched"])


class SessionOrchestratorAdminCommandLoggingTests(unittest.TestCase):
    def test_execute_admin_open_logs_execution_record(self):
        class RepoFake:
            def __init__(self):
                self.logged = []

            def get_locker(self, sector_id, locker_id):
                return {"activeBookingId": "book-7"}

            def delete_admin_command(self, **kwargs):
                return None

            def log_admin_command_execution(self, **kwargs):
                self.logged.append(kwargs)

        class MqttFake:
            def __init__(self):
                self.published = []

            def publish_json(self, *, topic, payload):
                self.published.append((topic, payload))

        repo = RepoFake()
        mqtt = MqttFake()
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

        orchestrator._execute_admin_open(
            locker_id="L1",
            cmd_id="cmd-9",
            payload={"cmd": "OPEN", "actorUid": "admin-3"},
        )

        self.assertEqual(len(mqtt.published), 2)
        self.assertEqual(len(repo.logged), 1)
        self.assertEqual(repo.logged[0]["status"], "EXECUTED")
        self.assertEqual(repo.logged[0]["sector_id"], "S1")
        self.assertEqual(repo.logged[0]["locker_id"], "L1")
        self.assertEqual(repo.logged[0]["cmd_id"], "cmd-9")

