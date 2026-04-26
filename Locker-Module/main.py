from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

from Locker_Module import LockerConfig, LockerManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("droplock.main")


@dataclass
class MqttConfig:
    broker_host: str = "zero-day.local"
    broker_port: int = 8883
    sector_id: str = "S1"
    client_id: str = "droplock-controller-1"
    heartbeat_seconds: int = 30

    tls_enabled: bool = True
    tls_ca_cert_path: str = "/home/droplock/Projects/DropLock/Keys/DropLock-TLS/ca.crt"
    tls_client_cert_path: str = "/home/droplock/Projects/DropLock/Keys/DropLock-TLS/client.crt"
    tls_client_key_path: str = "/home/droplock/Projects/DropLock/Keys/DropLock-TLS/client.key"
    tls_skip_verify: bool = False


def now_ms() -> int:
    return int(time.time() * 1000)


class DropLockController:
    def __init__(self, mqtt_config: MqttConfig, locker_manager: LockerManager):
        self.cfg = mqtt_config
        self.lockers = locker_manager
        self.client = self._build_client()
        self.last_heartbeat = 0.0
        self._weight_streams = {}
        self._weight_stream_lock = threading.Lock()

    def _cmd_topic(self, locker_id: str) -> str:
        return f"droplock/{self.cfg.sector_id}/{locker_id}/cmd"

    def _event_topic(self, locker_id: str) -> str:
        return f"droplock/{self.cfg.sector_id}/{locker_id}/events"

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.cfg.client_id,
        )

        if self.cfg.tls_enabled:
            client.tls_set(
                ca_certs=self.cfg.tls_ca_cert_path or None,
                certfile=self.cfg.tls_client_cert_path or None,
                keyfile=self.cfg.tls_client_key_path or None,
                cert_reqs=ssl.CERT_NONE if self.cfg.tls_skip_verify else ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(self.cfg.tls_skip_verify)

        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        return client

    def publish_event(self, locker_id: str, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"))
        self.client.publish(self._event_topic(locker_id), payload=body, qos=1, retain=False)
        logger.info("Published event locker=%s payload=%s", locker_id, body)

    def emit_heartbeat(self):
        for locker_id, locker_status in self.lockers.heartbeat_snapshot().items():
            self.publish_event(
                locker_id,
                {
                    "schemaVersion": 1,
                    "type": "HEARTBEAT",
                    "ts": now_ms(),
                    "sectorId": self.cfg.sector_id,
                    "lockerId": locker_id,
                    "doorClosed": locker_status["doorClosed"],
                    "tamper": locker_status["tamper"],
                },
            )

    def handle_open(self, locker_id: str, cmd: dict):
        locker = self.lockers.get_locker(locker_id)
        request_id = cmd.get("requestId", "")
        if locker is None:
            logger.warning("OPEN for unknown locker_id=%s", locker_id)
            return

        locker.unlock(duration=0.5)

        self.publish_event(
            locker_id,
            {
                "schemaVersion": 1,
                "type": "OPEN_ACK",
                "requestId": request_id,
                "ts": now_ms(),
                "sectorId": self.cfg.sector_id,
                "lockerId": locker_id,
                "ok": True,
            },
        )

        self.publish_event(
            locker_id,
            {
                "schemaVersion": 1,
                "type": "DOOR_OPEN",
                "requestId": request_id,
                "ts": now_ms(),
                "sectorId": self.cfg.sector_id,
                "lockerId": locker_id,
            },
        )

        self._start_weight_stream(locker_id, request_id)

    def handle_close(self, locker_id: str, cmd: dict):
        locker = self.lockers.get_locker(locker_id)
        request_id = cmd.get("requestId", "")
        if locker is None:
            logger.warning("CLOSE for unknown locker_id=%s", locker_id)
            return

        self._stop_weight_stream(locker_id)
        locker.begin_close_wait()
        if locker.verify_closed(close_buffer_seconds=5):
            self.publish_event(
                locker_id,
                {
                    "schemaVersion": 1,
                    "type": "DOOR_CLOSED",
                    "requestId": request_id,
                    "ts": now_ms(),
                    "sectorId": self.cfg.sector_id,
                    "lockerId": locker_id,
                },
            )

            self.publish_event(
                locker_id,
                {
                    "schemaVersion": 1,
                    "type": "CLOSE_ACK",
                    "requestId": request_id,
                    "ts": now_ms(),
                    "sectorId": self.cfg.sector_id,
                    "lockerId": locker_id,
                    "ok": True,
                },
            )
        else:
            self.publish_event(
                locker_id,
                {
                    "schemaVersion": 1,
                    "type": "CLOSE_ACK",
                    "requestId": request_id,
                    "ts": now_ms(),
                    "sectorId": self.cfg.sector_id,
                    "lockerId": locker_id,
                    "ok": False,
                },
            )

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties=None):
        logger.info("Connected to MQTT rc=%s", reason_code)
        for locker_id in self.lockers.lockers.keys():
            topic = self._cmd_topic(locker_id)
            logger.info("Subscribing to %s", topic)
            client.subscribe(topic, qos=1)

    def on_disconnect(self, client: mqtt.Client, userdata, disconnect_flags, reason_code, properties=None):
        logger.warning("Disconnected from MQTT rc=%s", reason_code)

    def _locker_id_from_topic(self, topic: str) -> Optional[str]:
        # expected: droplock/{sector}/{lockerId}/cmd
        parts = topic.split("/")
        if len(parts) != 4:
            return None
        if parts[0] != "droplock" or parts[1] != self.cfg.sector_id or parts[3] != "cmd":
            return None
        return parts[2]

    def on_message(self, client: mqtt.Client, userdata, msg):
        locker_id = self._locker_id_from_topic(msg.topic)
        if locker_id is None:
            logger.warning("Ignoring message on unexpected topic=%s", msg.topic)
            return

        try:
            cmd = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON cmd: %r", msg.payload)
            return

        cmd_type = cmd.get("type")
        logger.info("Received cmd locker=%s type=%s payload=%s", locker_id, cmd_type, cmd)
        if cmd_type == "OPEN":
            self.handle_open(locker_id, cmd)
        elif cmd_type == "CLOSE":
            self.handle_close(locker_id, cmd)

    def _start_weight_stream(self, locker_id: str, request_id: str):
        self._stop_weight_stream(locker_id)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._weight_stream_loop,
            args=(locker_id, request_id, stop_event),
            daemon=True,
        )
        with self._weight_stream_lock:
            self._weight_streams[locker_id] = {
                "thread": thread,
                "stop_event": stop_event,
            }
        thread.start()

    def _stop_weight_stream(self, locker_id: str):
        with self._weight_stream_lock:
            stream = self._weight_streams.pop(locker_id, None)
        if not stream:
            return

        stream["stop_event"].set()
        stream["thread"].join(timeout=2.0)

    def _weight_stream_loop(self, locker_id: str, request_id: str, stop_event: threading.Event):
        while not stop_event.is_set():
            locker = self.lockers.get_locker(locker_id)
            if locker is None:
                return

            measured_weight = locker.get_weight_grams()
            logger.info(
                "Weight debug locker=%s request_id=%s measured_weight_grams=%s",
                locker_id,
                request_id,
                measured_weight,
            )
            self.publish_event(
                locker_id,
                {
                    "schemaVersion": 1,
                    "type": "WEIGHT_MEASURED",
                    "requestId": request_id,
                    "ts": now_ms(),
                    "sectorId": self.cfg.sector_id,
                    "lockerId": locker_id,
                    "weightGrams": measured_weight,
                    "measuredWeightGrams": measured_weight,
                },
            )
            stop_event.wait(1.0)

    def run_forever(self):
        self.client.connect(self.cfg.broker_host, self.cfg.broker_port, keepalive=60)
        self.client.loop_start()
        try:
            while True:
                now = time.monotonic()
                if now - self.last_heartbeat >= self.cfg.heartbeat_seconds:
                    self.emit_heartbeat()
                    self.last_heartbeat = now
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Stopping DropLock locker controller")
        finally:
            for locker_id in list(self.lockers.lockers.keys()):
                self._stop_weight_stream(locker_id)
            self.client.loop_stop()
            self.client.disconnect()
            GPIO.cleanup()


def build_default_locker_configs():
    # Locker-1 exists today; layout is ready to scale to 4 lockers.
    return [
        LockerConfig(locker_id="Locker 1", relay_pin=17, door_pin=27, mc38_pin=23),
    ]


def _read_known_weight(locker_id: str, index: int) -> float:
    while True:
        value = input(f"Locker {locker_id} - enter known weight for object #{index} in grams: ").strip()
        try:
            grams = float(value)
        except ValueError:
            print("Invalid number, please try again.")
            continue

        if grams <= 0:
            print("Weight must be greater than 0.")
            continue
        return grams


def _run_manual_weight_calibration(locker_manager: LockerManager) -> Dict[str, bool]:
    print("\nManual initial calibration (3 objects per locker)")
    print("Use three known objects and place only one object on the scale per step.")
    print("Remove all weight from each scale before starting.\n")

    known_weights_by_locker: Dict[str, list[float]] = {}
    for locker_id in locker_manager.lockers.keys():
        print(f"Preparing manual calibration for {locker_id}.")
        known_weights_by_locker[locker_id] = [_read_known_weight(locker_id, i) for i in (1, 2, 3)]

    def _prompt_before_read(index, known_weight):
        input(
            f"\nPlace object #{index} ({known_weight}g) on the scale, wait for it to settle, then press Enter..."
        )

    calibration_status = locker_manager.calibrate_weight_sensors_manual(
        known_weights_by_locker,
        before_read=_prompt_before_read,
    )
    return calibration_status


def main() -> int:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    locker_manager = LockerManager.from_configs(build_default_locker_configs())
    calibration_status = _run_manual_weight_calibration(locker_manager)
    logger.info("Manual weight sensor calibration status: %s", calibration_status)

    if not all(calibration_status.values()):
        logger.error("Manual calibration failed for at least one locker; aborting startup.")
        GPIO.cleanup()
        return 1

    controller = DropLockController(MqttConfig(), locker_manager)
    controller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
