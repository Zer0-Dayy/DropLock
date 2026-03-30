"""Hardcoded mock locker controller for Raspberry Pi tests.

Run on spare Pi to simulate the locker side:
    python mock_controller_hardcoded.py

This script listens for OPEN/CLOSE commands and publishes corresponding events
using the agreed MQTT contract.
"""

from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

# --------------------------
# Hardcoded test parameters
# --------------------------
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 8883
SECTOR_ID = "S1"
LOCKER_ID = "Locker 1"
CLIENT_ID = "droplock-mock-controller-pi"
HEARTBEAT_SECONDS = 5

TLS_ENABLED = False
TLS_CA_CERT_PATH = ""
TLS_CLIENT_CERT_PATH = ""
TLS_CLIENT_KEY_PATH = ""
TLS_SKIP_VERIFY = False

CMD_TOPIC = f"droplock/{SECTOR_ID}/{LOCKER_ID}/cmd"
EVENT_TOPIC = f"droplock/{SECTOR_ID}/{LOCKER_ID}/events"


@dataclass
class LockerState:
    door_closed: bool = True
    tamper_flag: bool = False


state = LockerState()
last_heartbeat = 0.0


def now_ms() -> int:
    return int(time.time() * 1000)


def publish_event(client: mqtt.Client, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":"))
    client.publish(EVENT_TOPIC, payload=body, qos=1, retain=False)
    print(f"[EVENT] {body}")


def emit_heartbeat(client: mqtt.Client) -> None:
    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "HEARTBEAT",
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
            "doorClosed": state.door_closed,
            "tamper": state.tamper_flag,
        },
    )


def handle_open(client: mqtt.Client, cmd: dict) -> None:
    request_id = cmd.get("requestId", "")

    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "OPEN_ACK",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
            "ok": True,
        },
    )

    # Simulate that locker opens and sends weight updates.
    state.door_closed = False
    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "DOOR_OPEN",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
        },
    )

    # First weight (possibly invalid)
    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "WEIGHT_MEASURED",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
            "weightGrams": 900,
        },
    )

    # Second weight (valid for many tests)
    time.sleep(1)
    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "WEIGHT_MEASURED",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
            "weightGrams": 1500,
        },
    )


def handle_close(client: mqtt.Client, cmd: dict) -> None:
    request_id = cmd.get("requestId", "")

    state.door_closed = True
    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "DOOR_CLOSED",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
        },
    )

    publish_event(
        client,
        {
            "schemaVersion": 1,
            "type": "CLOSE_ACK",
            "requestId": request_id,
            "ts": now_ms(),
            "sectorId": SECTOR_ID,
            "lockerId": LOCKER_ID,
            "ok": True,
        },
    )


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None):
    print(f"Connected rc={reason_code}; subscribing {CMD_TOPIC}")
    client.subscribe(CMD_TOPIC, qos=1)


def on_message(client: mqtt.Client, userdata, msg):
    try:
        cmd = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError:
        print(f"Invalid JSON cmd: {msg.payload!r}")
        return

    print(f"[CMD] {cmd}")
    cmd_type = cmd.get("type")
    if cmd_type == "OPEN":
        handle_open(client, cmd)
    elif cmd_type == "CLOSE":
        handle_close(client, cmd)


def build_client() -> mqtt.Client:
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)

    if TLS_ENABLED:
        client.tls_set(
            ca_certs=TLS_CA_CERT_PATH or None,
            certfile=TLS_CLIENT_CERT_PATH or None,
            keyfile=TLS_CLIENT_KEY_PATH or None,
            cert_reqs=ssl.CERT_NONE if TLS_SKIP_VERIFY else ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.tls_insecure_set(TLS_SKIP_VERIFY)

    client.on_connect = on_connect
    client.on_message = on_message
    return client


def main() -> int:
    global last_heartbeat
    client = build_client()
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                emit_heartbeat(client)
                last_heartbeat = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Stopping mock controller")
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
