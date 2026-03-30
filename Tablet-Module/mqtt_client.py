from __future__ import annotations

import json
import logging
import queue
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import paho.mqtt.client as mqtt


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MQTTIncomingMessage:
    topic: str
    payload: str
    qos: int
    retain: bool
    received_at: float


class MQTTClient:
    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        sector_id: str,
        broker_host: str,
        broker_port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        keepalive: int = 60,
        event_queue_maxsize: int = 100,
        connect_timeout_s: float = 5.0,
        qos_subscribe: int = 1,
        qos_publish: int = 1,
        clean_session: bool = True,
        enable_tls: bool = False,
        tls_ca_cert_path: str | None = None,
        tls_client_cert_path: str | None = None,
        tls_client_key_path: str | None = None,
        tls_insecure_skip_verify: bool = False,
        protocol: int = mqtt.MQTTv311,
    ) -> None:
        if not sector_id or not sector_id.strip():
            raise ValueError("sector_id must be a non-empty string")
        if not broker_host or not broker_host.strip():
            raise ValueError("broker_host must be a non-empty string")
        if broker_port <= 0:
            raise ValueError("broker_port must be > 0")
        if not client_id or not client_id.strip():
            raise ValueError("client_id must be a non-empty string")
        if keepalive <= 0:
            raise ValueError("keepalive must be > 0")
        if event_queue_maxsize <= 0:
            raise ValueError("event_queue_maxsize must be > 0")
        if connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be > 0")

        self._sector_id = sector_id
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._keepalive = keepalive
        self._connect_timeout_s = connect_timeout_s
        self._qos_subscribe = qos_subscribe
        self._qos_publish = qos_publish
        self._clean_session = clean_session
        self._enable_tls = enable_tls
        self._tls_ca_cert_path = tls_ca_cert_path
        self._tls_client_cert_path = tls_client_cert_path
        self._tls_client_key_path = tls_client_key_path
        self._tls_insecure_skip_verify = tls_insecure_skip_verify
        self._protocol = protocol

        self._event_queue: queue.Queue[MQTTIncomingMessage] = queue.Queue(
            maxsize=event_queue_maxsize
        )

        self._client: mqtt.Client | None = None
        self._running = False
        self._connected = False
        self._state_lock = threading.Lock()
        self._connected_event = threading.Event()

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                raise RuntimeError("MQTT client already started")

            self._client = self._build_client()
            self._running = True
            self._connected = False
            self._connected_event.clear()

        logger.info(
            "Starting MQTT client client_id=%s host=%s port=%s sector_id=%s tls=%s",
            self._client_id,
            self._broker_host,
            self._broker_port,
            self._sector_id,
            self._enable_tls,
        )

        try:
            # connect() schedules the connection; loop_start() handles network traffic.
            self._client.connect(
                host=self._broker_host,
                port=self._broker_port,
                keepalive=self._keepalive,
            )
            self._client.loop_start()
        except Exception:
            with self._state_lock:
                self._running = False
                self._connected = False
                self._client = None
            logger.exception("Failed to start MQTT client")
            raise

        if not self._connected_event.wait(timeout=self._connect_timeout_s):
            self.stop()
            raise RuntimeError(
                f"MQTT connection timed out after {self._connect_timeout_s:.1f} s"
            )

    def stop(self) -> None:
        client = self._client

        with self._state_lock:
            if not self._running and client is None:
                return

            self._running = False
            self._connected = False
            self._connected_event.clear()

        if client is not None:
            logger.info("Stopping MQTT client client_id=%s", self._client_id)

            try:
                client.loop_stop()
            except Exception:
                logger.exception("MQTT loop_stop() failed")

            try:
                client.disconnect()
            except Exception:
                logger.exception("MQTT disconnect() failed")

        with self._state_lock:
            self._client = None

    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    def publish_open(
        self,
        *,
        request_id: str,
        locker_id: str,
        actor_uid: str,
        booking_id: str,
        token_id: str,
    ) -> None:
        payload = self._build_open_payload(
            request_id=request_id,
            locker_id=locker_id,
            actor_uid=actor_uid,
            booking_id=booking_id,
            token_id=token_id,
        )
        topic = self._command_topic(locker_id)
        self.publish_json(topic=topic, payload=payload)

    def publish_close(
        self,
        *,
        request_id: str,
        locker_id: str,
        actor_uid: str,
        booking_id: str,
        token_id: str,
    ) -> None:
        payload = self._build_close_payload(
            request_id=request_id,
            locker_id=locker_id,
            actor_uid=actor_uid,
            booking_id=booking_id,
            token_id=token_id,
        )
        topic = self._command_topic(locker_id)
        self.publish_json(topic=topic, payload=payload)

    def publish_json(self, *, topic: str, payload: dict[str, Any]) -> None:
        client = self._require_active_client()

        payload_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        logger.info(
            "Publishing MQTT message topic=%s payload=%s",
            topic,
            payload_text,
        )

        info = client.publish(
            topic=topic,
            payload=payload_text,
            qos=self._qos_publish,
            retain=False,
        )

        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(
                f"MQTT publish failed rc={info.rc} topic={topic}"
            )

    def get_message(self, timeout: float | None = None) -> MQTTIncomingMessage | None:
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_message_nowait(self) -> MQTTIncomingMessage | None:
        try:
            return self._event_queue.get_nowait()
        except queue.Empty:
            return None

    def _command_topic(self, locker_id: str) -> str:
        self._validate_non_empty(locker_id, "locker_id")
        return f"droplock/{self._sector_id}/{locker_id}/cmd"

    def _event_topic(self, locker_id: str) -> str:
        self._validate_non_empty(locker_id, "locker_id")
        return f"droplock/{self._sector_id}/{locker_id}/events"

    def _event_wildcard_topic(self) -> str:
        return f"droplock/{self._sector_id}/+/events"

    def _build_open_payload(
        self,
        *,
        request_id: str,
        locker_id: str,
        actor_uid: str,
        booking_id: str,
        token_id: str,
    ) -> dict[str, Any]:
        self._validate_non_empty(request_id, "request_id")
        self._validate_non_empty(locker_id, "locker_id")
        self._validate_non_empty(actor_uid, "actor_uid")
        self._validate_non_empty(booking_id, "booking_id")
        self._validate_non_empty(token_id, "token_id")

        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "type": "OPEN",
            "requestId": request_id,
            "ts": self._now_ms(),
            "sectorId": self._sector_id,
            "lockerId": locker_id,
            "actorUid": actor_uid,
            "bookingId": booking_id,
            "tokenId": token_id,
        }

    def _build_close_payload(
        self,
        *,
        request_id: str,
        locker_id: str,
        actor_uid: str,
        booking_id: str,
        token_id: str,
    ) -> dict[str, Any]:
        self._validate_non_empty(request_id, "request_id")
        self._validate_non_empty(locker_id, "locker_id")
        self._validate_non_empty(actor_uid, "actor_uid")
        self._validate_non_empty(booking_id, "booking_id")
        self._validate_non_empty(token_id, "token_id")

        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "type": "CLOSE",
            "requestId": request_id,
            "ts": self._now_ms(),
            "sectorId": self._sector_id,
            "lockerId": locker_id,
            "actorUid": actor_uid,
            "bookingId": booking_id,
            "tokenId": token_id,
        }

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            clean_session=self._clean_session,
            protocol=self._protocol,
            transport="tcp",
        )

        if self._username:
            client.username_pw_set(self._username, self._password)

        if self._enable_tls:
            self._configure_tls(client)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # Useful for troubleshooting callback exceptions during development.
        client.suppress_exceptions = False
        return client

    def _configure_tls(self, client: mqtt.Client) -> None:
        tls_kwargs: dict[str, Any] = {
            "ca_certs": self._tls_ca_cert_path,
            "certfile": self._tls_client_cert_path,
            "keyfile": self._tls_client_key_path,
            "tls_version": ssl.PROTOCOL_TLS_CLIENT,
        }

        client.tls_set(**tls_kwargs)
        client.tls_insecure_set(self._tls_insecure_skip_verify)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: Any,
    ) -> None:
        """
        Subscribe to the sector event wildcard once connected.
        """
        if getattr(reason_code, "is_failure", False):
            logger.error(
                "MQTT connection failed client_id=%s reason_code=%s",
                self._client_id,
                reason_code,
            )
            with self._state_lock:
                self._connected = False
            self._connected_event.clear()
            return

        with self._state_lock:
            self._connected = True
        self._connected_event.set()

        topic = self._event_wildcard_topic()
        logger.info(
            "MQTT connected client_id=%s reason_code=%s subscribing=%s qos=%s",
            self._client_id,
            reason_code,
            topic,
            self._qos_subscribe,
        )

        result, mid = client.subscribe(topic, qos=self._qos_subscribe)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.error(
                "MQTT subscribe failed result=%s mid=%s topic=%s",
                result,
                mid,
                topic,
            )
            return

        logger.info("MQTT subscribe sent mid=%s topic=%s", mid, topic)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: Any,
    ) -> None:
        with self._state_lock:
            self._connected = False
        self._connected_event.clear()

        logger.warning(
            "MQTT disconnected client_id=%s reason_code=%s",
            self._client_id,
            reason_code,
        )

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        received_at = time.monotonic()

        try:
            payload_text = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.exception(
                "Failed to decode MQTT payload as UTF-8 topic=%s",
                msg.topic,
            )
            return

        envelope = MQTTIncomingMessage(
            topic=msg.topic,
            payload=payload_text,
            qos=msg.qos,
            retain=msg.retain,
            received_at=received_at,
        )

        try:
            self._event_queue.put_nowait(envelope)
        except queue.Full:
            logger.warning(
                "MQTT inbound queue full; dropping newest message topic=%s payload=%s",
                msg.topic,
                payload_text,
            )
            return

        logger.info(
            "MQTT message received topic=%s qos=%s retain=%s payload=%s",
            msg.topic,
            msg.qos,
            msg.retain,
            payload_text,
        )

    def _require_active_client(self) -> mqtt.Client:
        with self._state_lock:
            if not self._running or self._client is None:
                raise RuntimeError("MQTT client not started")
            if not self._connected:
                raise RuntimeError("MQTT client not connected")
            return self._client

    @staticmethod
    def _validate_non_empty(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
