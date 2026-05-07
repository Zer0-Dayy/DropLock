from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from session_models import (
    CloseGateResult,
    ControllerEvent,
    ControllerEventType,
    DeviceContext,
    LockerSession,
    SessionPhase,
    SignatureResult,
    ValidationResult,
)
from close_gates import CloseGates


logger = logging.getLogger(__name__)


class SessionOrchestrator:
    PURPOSE_COURIER_DROP = "COURIER_DROP"
    PURPOSE_USER_PICKUP = "USER_PICKUP"

    def __init__(
        self,
        *,
        device_context: DeviceContext,
        scanner_input: Any,
        access_validator: Any,
        mqtt_client: Any,
        controller_event_parser: Any,
        firebase_repo: Any,
        signature_capture: Any,
        close_gates: Any,
        token_service: Any | None = None,
        ui_controller: Any | None = None,
        event_logger: Any | None = None,
        email_notifier: Any | None = None,
        idle_sleep_s: float = 0.05,
        mqtt_poll_timeout_s: float = 0.05,
        open_ack_timeout_s: float = 20.0,
        close_ack_timeout_s: float = 20.0,
        mqtt_start_retry_delay_s: float = 2.0,
        mqtt_start_max_attempts: int | None = None,
        admin_command_poll_interval_s: float = 0.5,
    ) -> None:
        self._device_context = device_context
        self._scanner_input = scanner_input
        self._access_validator = access_validator
        self._mqtt_client = mqtt_client
        self._controller_event_parser = controller_event_parser
        self._firebase_repo = firebase_repo
        self._signature_capture = signature_capture
        self._close_gates = close_gates
        self._token_service = token_service
        self._ui_controller = ui_controller
        self._event_logger = event_logger
        self._email_notifier = email_notifier

        self._idle_sleep_s = idle_sleep_s
        self._mqtt_poll_timeout_s = mqtt_poll_timeout_s
        self._open_ack_timeout_s = open_ack_timeout_s
        self._close_ack_timeout_s = close_ack_timeout_s
        self._mqtt_start_retry_delay_s = mqtt_start_retry_delay_s
        self._mqtt_start_max_attempts = mqtt_start_max_attempts
        self._admin_command_poll_interval_s = max(0.1, float(admin_command_poll_interval_s))

        self._running = False
        self._active_session: LockerSession | None = None
        self._active_validation: ValidationResult | None = None
        self._active_open_sent_mono: float | None = None
        self._active_close_sent_mono: float | None = None
        self._active_cancel_close_pending = False

        self._locker_door_closed: dict[str, bool] = {}
        self._tamper_alert_active: dict[str, bool] = {}
        self._admin_command_last_poll_mono = 0.0
        self._admin_open_in_flight: set[str] = set()
        self._admin_open_pending_ack: set[tuple[str, str]] = set()
        self._admin_close_retry_payloads: dict[tuple[str, str], tuple[str, dict[str, Any], dict[str, Any]]] = {}

    def run_forever(self) -> None:
        self.start()
        try:
            while self._running:
                did_work = False
                did_work |= self._process_next_mqtt_message()
                did_work |= self._process_cancel_request()
                did_work |= self._process_next_scan()
                did_work |= self._check_session_timeouts()
                did_work |= self._process_next_admin_command()
                if not did_work:
                    time.sleep(self._idle_sleep_s)
        finally:
            logger.info("Session orchestrator loop ended")

    def start(self) -> None:
        self._ensure_scanner_started()
        self._ensure_mqtt_started_with_retry()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def _process_next_scan(self) -> bool:
        scan = self._get_next_scan()
        if scan is None:
            return False

        token_id = getattr(scan, "normalized_text", None) or str(scan).strip()
        if not token_id:
            return True

        self._log_event("SCAN_RECEIVED", token_id=token_id)

        if self._active_session is not None:
            self._show_busy_ui(token_id)
            return True

        self._show_processing_request_ui(token_id)
        validation = self._validate_token(token_id)

        if not validation.allowed:
            reason = validation.reason or "DENIED"
            self._show_denied_ui(token_id, reason)
            self._log_event("QR_DENIED", token_id=token_id, reason=reason)
            return True

        session = self._build_session(token_id, validation)
        self._active_session = session
        self._active_validation = validation

        self._show_unlocking_ui(session)
        try:
            self._publish_open(session)
        except Exception as exc:
            logger.warning("Failed to publish OPEN request_id=%s locker_id=%s: %s", session.request_id, session.locker_id, exc)
            self._show_error_ui("Controller offline. Please scan again.")
            self._clear_active_session()
            return True

        self._create_unlock_records(granted=True, reason="OK", session=session)
        self._active_open_sent_mono = time.monotonic()
        return True

    def _get_next_scan(self) -> Any | None:
        scan = self._scanner_input.get_scan_nowait()
        if scan is not None:
            return scan

        if self._ui_controller and hasattr(self._ui_controller, "get_scan_nowait"):
            scan_text = self._ui_controller.get_scan_nowait()
            if scan_text:
                return scan_text

        return None

    def _process_next_mqtt_message(self) -> bool:
        raw_msg = self._mqtt_client.get_message(timeout=self._mqtt_poll_timeout_s)
        if raw_msg is None:
            return False

        event = self._controller_event_parser.parse(raw_msg)
        if event is None:
            return True

        self._route_event(event)
        return True

    def _process_cancel_request(self) -> bool:
        if not self._ui_controller or not hasattr(self._ui_controller, "get_cancel_nowait"):
            return False

        if not self._ui_controller.get_cancel_nowait():
            return False

        if self._active_session is None:
            return True

        if self._active_session.phase in (SessionPhase.UNLOCKING, SessionPhase.CLOSING):
            self._show_error_ui("Cancellation is unavailable while controller commands are in flight.")
            return True

        self._cancel_active_session(source="TABLET_BUTTON")
        return True

    def _route_event(self, event: ControllerEvent) -> None:
        if event.event_type in (ControllerEventType.HEARTBEAT, ControllerEventType.TAMPER):
            self._handle_background(event)
            return

        if self._active_session is None:
            if event.event_type in (ControllerEventType.DOOR_OPEN, ControllerEventType.DOOR_CLOSED):
                self._locker_door_closed[event.locker_id] = event.event_type == ControllerEventType.DOOR_CLOSED
            return

        if event.locker_id != self._active_session.locker_id:
            return
        if event.request_id and event.request_id != self._active_session.request_id:
            return

        if event.event_type == ControllerEventType.OPEN_ACK:
            self._on_open_ack(event)
        elif event.event_type == ControllerEventType.OPEN_DENIED:
            self._fail_active_session("OPEN_DENIED")
        elif event.event_type == ControllerEventType.WEIGHT_MEASURED:
            self._on_weight_measured(event)
        elif event.event_type == ControllerEventType.DOOR_OPEN:
            self._locker_door_closed[event.locker_id] = False
        elif event.event_type == ControllerEventType.DOOR_CLOSED:
            self._locker_door_closed[event.locker_id] = True
        elif event.event_type == ControllerEventType.CLOSE_ACK:
            self._on_close_ack(event)

    def _handle_background(self, event: ControllerEvent) -> None:
        if event.event_type == ControllerEventType.HEARTBEAT:
            payload = event.payload or {}
            heartbeat_ts = self._extract_ts_ms(payload)
            self._firebase_repo.update_locker_heartbeat(
                sector_id=self._device_context.sector_id,
                locker_id=event.locker_id,
                heartbeat_at_ms=heartbeat_ts,
            )
            if "tamper" in payload:
                tamper_flag = self._coerce_bool(payload.get("tamper"))
                self._firebase_repo.update_locker_tamper(
                    sector_id=self._device_context.sector_id,
                    locker_id=event.locker_id,
                    tamper_flag=tamper_flag,
                    tamper_at_ms=heartbeat_ts,
                )
                self._notify_tamper_if_needed(
                    locker_id=event.locker_id,
                    tamper_flag=tamper_flag,
                    ts_ms=heartbeat_ts,
                )
            return
        if event.event_type == ControllerEventType.TAMPER:
            self._firebase_repo.update_locker_tamper(
                sector_id=self._device_context.sector_id,
                locker_id=event.locker_id,
                tamper_flag=True,
                tamper_at_ms=self._extract_ts_ms(event.payload),
            )
            self._notify_tamper_if_needed(
                locker_id=event.locker_id,
                tamper_flag=True,
                ts_ms=self._extract_ts_ms(event.payload),
            )

    def _on_open_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        self._append_booking_event("UNLOCK_GRANTED", {"requestId": self._active_session.request_id})

        purpose = self._purpose()
        if purpose == self.PURPOSE_COURIER_DROP:
            self._active_session = replace(self._active_session, phase=SessionPhase.WAITING_FOR_OTHER_GATES)
            self._show_locker_open_ui(self._active_session)
            self._show_weight_wait_ui(self._active_session, "Waiting for weight measurement")
            return

        self._active_session = replace(self._active_session, phase=SessionPhase.WAITING_FOR_SIGNATURE)
        self._show_locker_open_ui(self._active_session)
        self._show_signature_ui(self._active_session)
        self._capture_signature_and_close_if_ready()

    def _on_weight_measured(self, event: ControllerEvent) -> None:
        assert self._active_session is not None
        if self._purpose() != self.PURPOSE_COURIER_DROP:
            return
        if self._active_session.phase != SessionPhase.WAITING_FOR_OTHER_GATES:
            # Ignore late/duplicate weight events after we've moved on.
            return

        payload = event.payload or {}
        weight = payload.get("weightGrams")
        if weight is None:
            weight = payload.get("measuredWeightGrams")
        try:
            measured = int(weight)
        except Exception:
            self._log_event("WEIGHT_PARSE_FAILED", raw_weight=weight, payload=payload)
            return

        self._active_session = replace(self._active_session, weight_measured_grams=measured)
        accepted, reason = self._close_gates.evaluate_weight_only(self._active_session)
        self._active_session = replace(self._active_session, weight_accepted=accepted)

        self._persist_weight_update_async(
            booking_id=self._active_session.booking_id,
            measured=measured,
            accepted=accepted,
            reason=reason,
        )

        if not accepted:
            self._show_weight_wait_ui(self._active_session, reason)
            return

        self._active_session = replace(self._active_session, phase=SessionPhase.WAITING_FOR_SIGNATURE)
        self._show_signature_ui(self._active_session)
        self._capture_signature_and_close_if_ready()

    def _capture_signature_and_close_if_ready(self) -> None:
        assert self._active_session is not None

        signer_role = "courier" if self._purpose() == self.PURPOSE_COURIER_DROP else "user"
        signature: SignatureResult = self._signature_capture.capture_signature(
            session=self._active_session,
            signer_role=signer_role,
            prompt_text="Please sign before closing the locker.",
        )

        if not signature.captured or not signature.valid:
            if signature.validation_reason == "SIGNATURE_CAPTURE_CANCELLED":
                self._cancel_active_session(source="SIGNATURE_CAPTURE")
                return
            self._show_signature_failed_ui(signature.validation_reason)
            return

        self._active_session = replace(
            self._active_session,
            signature_captured_at=signature.signed_at,
            signature_path=signature.local_file_path,
            phase=SessionPhase.READY_TO_CLOSE,
        )

        self._attempt_close_if_ready()

    def _attempt_close_if_ready(self) -> None:
        assert self._active_session is not None
        require_weight = self._purpose() == self.PURPOSE_COURIER_DROP
        close_gates = self._close_gates

        config = getattr(close_gates, "_config", None)
        if config is not None and getattr(config, "require_weight", None) != require_weight:
            close_gates = CloseGates(config=replace(config, require_weight=require_weight))

        result: CloseGateResult = close_gates.evaluate(
            self._active_session,
            door_closed=self._locker_door_closed.get(self._active_session.locker_id),
        )
        if not result.can_close:
            self._show_close_blocked_ui(result.blocking_reasons or [])
            return

        self._active_session = replace(self._active_session, phase=SessionPhase.CLOSING)
        self._show_closing_ui(self._active_session)
        try:
            self._publish_close(self._active_session)
        except Exception as exc:
            logger.warning(
                "Failed to publish CLOSE request_id=%s locker_id=%s: %s",
                self._active_session.request_id,
                self._active_session.locker_id,
                exc,
            )
            self._active_session = replace(self._active_session, phase=SessionPhase.READY_TO_CLOSE)
            self._active_close_sent_mono = None
            self._show_error_ui("Controller offline while closing. Retrying...")
            return

        self._active_close_sent_mono = time.monotonic()

    def _cancel_active_session(self, *, source: str, actor_uid: str | None = None) -> bool:
        if self._active_session is None:
            return False

        if self._active_session.phase in (SessionPhase.UNLOCKING, SessionPhase.CLOSING):
            self._show_error_ui("Cancellation is unavailable while controller commands are in flight.")
            return False

        session_before_close = self._active_session
        try:
            self._publish_close(session_before_close)
        except Exception as exc:
            logger.warning(
                "Failed to publish cancellation CLOSE request_id=%s locker_id=%s: %s",
                session_before_close.request_id,
                session_before_close.locker_id,
                exc,
            )
            self._active_close_sent_mono = None
            self._show_error_ui("Controller offline while cancelling. Please retry cancellation.")
            return False

        try:
            self._append_booking_event(
                "SESSION_CANCELLED",
                {
                    "phase": session_before_close.phase.value,
                    "source": source,
                    "actorUid": actor_uid or self._device_context.device_uid,
                    "closeRequestId": session_before_close.request_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to append cancellation event booking_id=%s locker_id=%s",
                session_before_close.booking_id,
                session_before_close.locker_id,
            )
        self._active_cancel_close_pending = True
        self._active_session = replace(session_before_close, phase=SessionPhase.CLOSING)
        self._active_close_sent_mono = time.monotonic()
        self._show_operation_cancelled_ui()
        return True

    def _persist_weight_update_async(
        self,
        *,
        booking_id: str,
        measured: int,
        accepted: bool,
        reason: str,
    ) -> None:
        def _write() -> None:
            try:
                self._firebase_repo.update_booking_measured_weight(
                    booking_id=booking_id,
                    measured_weight_grams=measured,
                )
                self._append_booking_event(
                    "WEIGHT_MEASURED",
                    {"measuredWeightGrams": measured, "accepted": accepted, "reason": reason},
                )
            except Exception:
                logger.exception("Failed to persist measured weight booking_id=%s", booking_id)

        threading.Thread(target=_write, daemon=True, name="persist-weight").start()

    def _on_close_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        if self._active_cancel_close_pending:
            try:
                self._append_booking_event("SESSION_CANCEL_CLOSE_ACK", {"requestId": self._active_session.request_id})
            except Exception:
                logger.exception(
                    "Failed to append cancellation CLOSE ack event booking_id=%s locker_id=%s",
                    self._active_session.booking_id,
                    self._active_session.locker_id,
                )
            self._clear_active_session(show_idle=False)
            return

        purpose = self._purpose()
        if not self._mark_active_token_used():
            return

        try:
            self._firebase_repo.update_locker_state_post_session(
                sector_id=self._active_session.sector_id,
                locker_id=self._active_session.locker_id,
                booking_id=self._active_session.booking_id,
                purpose=purpose,
            )
            self._firebase_repo.update_booking_status_post_session(
                booking_id=self._active_session.booking_id,
                purpose=purpose,
            )
        except Exception as exc:
            logger.exception(
                "Failed post-session Firebase updates booking_id=%s locker_id=%s",
                self._active_session.booking_id,
                self._active_session.locker_id,
            )
            self._show_error_ui(f"Failed to finalize booking state: {exc}")
            self._clear_active_session()
            return

        if purpose == self.PURPOSE_COURIER_DROP:
            self._issue_pickup_token_and_notify(self._active_session.booking_id)

        self._show_completed_ui(self._active_session)
        self._clear_active_session()

    def _issue_pickup_token_and_notify(self, booking_id: str) -> None:
        if self._token_service is None:
            logger.warning("Token service missing; cannot issue USER_PICKUP token")
            return

        booking = self._firebase_repo.get_booking(booking_id) or {}
        user_id = booking.get("userId")
        profile = self._firebase_repo.get_profile(user_id) if user_id else None
        email = (profile or {}).get("email") or booking.get("userEmail")
        display_name = (profile or {}).get("displayName") or booking.get("userName") or "customer"

        try:
            issued = self._token_service.issue_user_pickup_token(booking_id)
            if email and self._email_notifier is not None:
                self._email_notifier.send_token_email_async(
                    to_email=email,
                    recipient_name=display_name,
                    booking_id=booking_id,
                    token_id=issued.token_id,
                    purpose=issued.purpose,
                )
            else:
                logger.warning(
                    "Cannot send pickup token email booking_id=%s user_id=%s email_present=%s notifier_present=%s",
                    booking_id,
                    user_id,
                    bool(email),
                    self._email_notifier is not None,
                )
        except Exception:
            logger.exception("Failed to issue pickup token / send notification booking_id=%s", booking_id)

    def _check_session_timeouts(self) -> bool:
        if self._active_session is None:
            return False

        now = time.monotonic()
        if self._active_session.phase == SessionPhase.READY_TO_CLOSE and self._active_close_sent_mono is None:
            mqtt_connected = hasattr(self._mqtt_client, "is_connected") and self._mqtt_client.is_connected()
            if mqtt_connected:
                self._attempt_close_if_ready()
                return True

        if self._active_session.phase == SessionPhase.UNLOCKING and self._active_open_sent_mono is not None:
            if now - self._active_open_sent_mono > self._open_ack_timeout_s:
                self._fail_active_session("OPEN_ACK_TIMEOUT")
                return True
        if self._active_session.phase == SessionPhase.CLOSING and self._active_close_sent_mono is not None:
            if now - self._active_close_sent_mono > self._close_ack_timeout_s:
                if not self._active_cancel_close_pending:
                    self._mark_active_token_used()
                self._fail_active_session("CLOSE_ACK_TIMEOUT")
                return True
        return False

    def _process_next_admin_command(self) -> bool:
        now_mono = time.monotonic()
        if now_mono - self._admin_command_last_poll_mono < self._admin_command_poll_interval_s:
            return False
        self._admin_command_last_poll_mono = now_mono

        try:
            sector_commands = self._firebase_repo.get_admin_commands(self._device_context.sector_id)
        except Exception:
            logger.exception(
                "Failed reading admin commands for sector_id=%s",
                self._device_context.sector_id,
            )
            return False

        for locker_id, command_map in sector_commands.items():
            if not isinstance(command_map, dict):
                continue
            for cmd_id, payload in command_map.items():
                if locker_id in self._admin_open_in_flight:
                    continue
                admin_cmd_key = (locker_id, cmd_id)
                if self._active_session is not None:
                    return self._process_admin_command_during_active_session(
                        locker_id=locker_id,
                        cmd_id=cmd_id,
                        payload=payload,
                    )
                if admin_cmd_key in self._admin_open_pending_ack:
                    try:
                        self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id, raise_on_error=True)
                    except Exception:
                        logger.exception(
                            "Failed retrying admin command ack cmd_id=%s locker_id=%s",
                            cmd_id,
                            locker_id,
                        )
                    else:
                        self._admin_open_pending_ack.discard(admin_cmd_key)
                        self._admin_close_retry_payloads.pop(admin_cmd_key, None)
                    return True
                if admin_cmd_key in self._admin_close_retry_payloads:
                    topic, close_payload, original_payload = self._admin_close_retry_payloads[admin_cmd_key]
                    self._admin_open_in_flight.add(locker_id)
                    threading.Thread(
                        target=self._retry_admin_close,
                        kwargs={
                            "locker_id": locker_id,
                            "cmd_id": cmd_id,
                            "topic": topic,
                            "close_payload": close_payload,
                            "original_payload": original_payload,
                        },
                        daemon=True,
                        name=f"admin-close-{locker_id}",
                    ).start()
                    return True
                if not isinstance(payload, dict):
                    acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id)
                    self._log_admin_command_execution(
                        locker_id=locker_id,
                        cmd_id=cmd_id,
                        payload={},
                        status="IGNORED_INVALID_PAYLOAD",
                        acked=acked,
                    )
                    return True
                if str(payload.get("cmd") or "").upper() != "OPEN":
                    acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id)
                    self._log_admin_command_execution(
                        locker_id=locker_id,
                        cmd_id=cmd_id,
                        payload=payload,
                        status="IGNORED_UNSUPPORTED_CMD",
                        acked=acked,
                    )
                    return True
                self._admin_open_in_flight.add(locker_id)
                threading.Thread(
                    target=self._execute_admin_open,
                    kwargs={"locker_id": locker_id, "cmd_id": cmd_id, "payload": payload},
                    daemon=True,
                    name=f"admin-open-{locker_id}",
                ).start()
                return True
        return False

    def _process_admin_command_during_active_session(
        self,
        *,
        locker_id: str,
        cmd_id: str,
        payload: Any,
    ) -> bool:
        if not isinstance(payload, dict):
            acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id)
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload={},
                status="IGNORED_INVALID_PAYLOAD",
                acked=acked,
            )
            return True

        cmd = str(payload.get("cmd") or "").upper()
        actor_uid = str(payload.get("actorUid") or "admin")

        if cmd == "CANCEL":
            if locker_id != self._active_session.locker_id:
                acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id)
                self._log_admin_command_execution(
                    locker_id=locker_id,
                    cmd_id=cmd_id,
                    payload=payload,
                    status="IGNORED_CANCEL_WRONG_LOCKER",
                    actor_uid=actor_uid,
                    acked=acked,
                )
                return True

            cancelled = self._cancel_active_session(source="ADMIN_DASHBOARD", actor_uid=actor_uid)
            acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id) if cancelled else False
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload=payload,
                status="CANCELLED_ACTIVE_TRANSACTION" if cancelled else "CANCEL_FAILED",
                actor_uid=actor_uid,
                booking_id=self._active_session.booking_id if self._active_session else None,
                token_id=self._active_session.token_id if self._active_session else None,
                close_request_id=self._active_session.request_id if self._active_session else None,
                close_dispatched=cancelled,
                acked=acked,
                error=None if cancelled else "CANCEL_CLOSE_DISPATCH_FAILED",
            )
            return True

        status = "IGNORED_ACTIVE_TRANSACTION" if cmd in {"OPEN", "CLOSE"} else "IGNORED_UNSUPPORTED_CMD"
        acked = self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id)
        self._log_admin_command_execution(
            locker_id=locker_id,
            cmd_id=cmd_id,
            payload=payload,
            status=status,
            actor_uid=actor_uid,
            acked=acked,
        )
        return True

    def _execute_admin_open(self, *, locker_id: str, cmd_id: str, payload: dict[str, Any]) -> None:
        admin_cmd_key = (locker_id, cmd_id)
        try:
            locker = self._firebase_repo.get_locker(self._device_context.sector_id, locker_id) or {}
            active_booking_id = str(locker.get("activeBookingId") or "").strip()
            actor_uid = str(payload.get("actorUid") or "admin")
            booking_id = active_booking_id or f"admin_{cmd_id}"
            token_id = f"admin_{cmd_id}"
            open_request_id = f"admin_{cmd_id}"
            close_request_id = f"admin_{cmd_id}_close"
            topic = f"droplock/{self._device_context.sector_id}/{locker_id}/cmd"
            now_ms = int(time.time() * 1000)

            self._mqtt_client.publish_json(
                topic=topic,
                payload={
                    "schemaVersion": 1,
                    "type": "OPEN",
                    "requestId": open_request_id,
                    "ts": now_ms,
                    "sectorId": self._device_context.sector_id,
                    "lockerId": locker_id,
                    "actorUid": actor_uid,
                    "bookingId": booking_id,
                    "tokenId": token_id,
                },
            )
            try:
                close_payload = {
                    "schemaVersion": 1,
                    "type": "CLOSE",
                    "requestId": close_request_id,
                    "ts": now_ms,
                    "sectorId": self._device_context.sector_id,
                    "lockerId": locker_id,
                    "actorUid": actor_uid,
                    "bookingId": booking_id,
                    "tokenId": token_id,
                }
                self._mqtt_client.publish_json(
                    topic=topic,
                    payload=close_payload,
                )
            except Exception:
                self._admin_close_retry_payloads[admin_cmd_key] = (topic, close_payload, payload)
                self._log_admin_command_execution(
                    locker_id=locker_id,
                    cmd_id=cmd_id,
                    payload=payload,
                    status="CLOSE_DISPATCH_FAILED_RETRY_QUEUED",
                    actor_uid=actor_uid,
                    booking_id=booking_id,
                    token_id=token_id,
                    open_request_id=open_request_id,
                    close_request_id=close_request_id,
                    close_dispatched=False,
                    acked=False,
                    error="CLOSE_DISPATCH_FAILED",
                )
                logger.exception(
                    "Failed dispatching admin CLOSE after OPEN cmd_id=%s locker_id=%s",
                    cmd_id,
                    locker_id,
                )
                return
            self._admin_open_pending_ack.add(admin_cmd_key)
            logger.info("Dispatched admin OPEN and attempted CLOSE cmd_id=%s locker_id=%s", cmd_id, locker_id)
            self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id, raise_on_error=True)
            self._admin_open_pending_ack.discard(admin_cmd_key)
            self._admin_close_retry_payloads.pop(admin_cmd_key, None)
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload=payload,
                status="EXECUTED",
                actor_uid=actor_uid,
                booking_id=booking_id,
                token_id=token_id,
                open_request_id=open_request_id,
                close_request_id=close_request_id,
                close_dispatched=True,
                acked=True,
            )
        except Exception:
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload=payload,
                status="EXECUTION_FAILED",
                acked=False,
                error="OPEN_OR_ACK_FAILED",
            )
            logger.exception("Failed handling admin OPEN cmd_id=%s locker_id=%s", cmd_id, locker_id)
        finally:
            self._admin_open_in_flight.discard(locker_id)

    def _retry_admin_close(
        self,
        *,
        locker_id: str,
        cmd_id: str,
        topic: str,
        close_payload: dict[str, Any],
        original_payload: dict[str, Any],
    ) -> None:
        admin_cmd_key = (locker_id, cmd_id)
        try:
            self._mqtt_client.publish_json(topic=topic, payload=close_payload)
            self._admin_open_pending_ack.add(admin_cmd_key)
            logger.info("Retried admin CLOSE cmd_id=%s locker_id=%s", cmd_id, locker_id)
            self._ack_admin_command(locker_id=locker_id, cmd_id=cmd_id, raise_on_error=True)
            self._admin_open_pending_ack.discard(admin_cmd_key)
            self._admin_close_retry_payloads.pop(admin_cmd_key, None)
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload=original_payload,
                status="EXECUTED_AFTER_CLOSE_RETRY",
                actor_uid=str(close_payload.get("actorUid") or "admin"),
                booking_id=str(close_payload.get("bookingId") or ""),
                token_id=str(close_payload.get("tokenId") or ""),
                open_request_id=f"admin_{cmd_id}",
                close_request_id=str(close_payload.get("requestId") or ""),
                close_dispatched=True,
                acked=True,
            )
        except Exception:
            self._log_admin_command_execution(
                locker_id=locker_id,
                cmd_id=cmd_id,
                payload=close_payload,
                status="CLOSE_RETRY_FAILED",
                acked=False,
                error="CLOSE_RETRY_FAILED",
            )
            logger.exception("Failed retrying admin CLOSE cmd_id=%s locker_id=%s", cmd_id, locker_id)
        finally:
            self._admin_open_in_flight.discard(locker_id)

    def _log_admin_command_execution(
        self,
        *,
        locker_id: str,
        cmd_id: str,
        payload: dict[str, Any],
        status: str,
        actor_uid: str | None = None,
        booking_id: str | None = None,
        token_id: str | None = None,
        open_request_id: str | None = None,
        close_request_id: str | None = None,
        close_dispatched: bool | None = None,
        acked: bool | None = None,
        error: str | None = None,
    ) -> None:
        if not hasattr(self._firebase_repo, "log_admin_command_execution"):
            return
        try:
            self._firebase_repo.log_admin_command_execution(
                sector_id=self._device_context.sector_id,
                locker_id=locker_id,
                cmd_id=cmd_id,
                status=status,
                command_payload=payload,
                actor_uid=actor_uid,
                booking_id=booking_id,
                token_id=token_id,
                open_request_id=open_request_id,
                close_request_id=close_request_id,
                close_dispatched=close_dispatched,
                acked=acked,
                error=error,
            )
        except Exception:
            logger.exception(
                "Failed logging admin command execution cmd_id=%s locker_id=%s status=%s",
                cmd_id,
                locker_id,
                status,
            )

    def _ack_admin_command(self, *, locker_id: str, cmd_id: str, raise_on_error: bool = False) -> bool:
        try:
            self._firebase_repo.delete_admin_command(
                sector_id=self._device_context.sector_id,
                locker_id=locker_id,
                cmd_id=cmd_id,
            )
            return True
        except Exception:
            logger.exception(
                "Failed deleting admin command cmd_id=%s locker_id=%s",
                cmd_id,
                locker_id,
            )
            if raise_on_error:
                raise
            return False

    def _mark_active_token_used(self) -> bool:
        if self._active_session is None:
            return False
        try:
            self._firebase_repo.mark_qr_token_used(token_id=self._active_session.token_id)
        except Exception as exc:
            logger.exception("Failed to mark QR token used token_id=%s", self._active_session.token_id)
            self._show_error_ui(f"Failed to finalize token usage: {exc}")
            self._clear_active_session()
            return False
        return True

    def _build_session(self, token_id: str, validation: ValidationResult) -> LockerSession:
        token_data = validation.token_data or {}
        booking_data = validation.booking_data or {}
        booking_id = str(token_data.get("bookingId") or "")
        return LockerSession(
            request_id=f"req_{uuid.uuid4().hex[:10]}",
            token_id=token_id,
            booking_id=booking_id,
            locker_id=str(token_data.get("lockerId") or booking_data.get("lockerId") or ""),
            sector_id=str(token_data.get("sectorId") or self._device_context.sector_id),
            device_uid=self._device_context.device_uid,
            phase=SessionPhase.UNLOCKING,
            created_at=datetime.now(timezone.utc),
            weight_expected_grams=booking_data.get("expectedWeightGrams"),
        )

    def _validate_token(self, token_id: str) -> ValidationResult:
        if hasattr(self._access_validator, "validate"):
            return self._access_validator.validate(token_id=token_id)
        return self._access_validator(token_id)

    def _create_unlock_records(self, *, granted: bool, reason: str, session: LockerSession) -> None:
        request_id = self._firebase_repo.create_unlock_request(
            token_id=session.token_id,
            booking_id=session.booking_id,
            sector_id=session.sector_id,
            locker_id=session.locker_id,
            actor_uid=self._device_context.device_uid,
            request_id=session.request_id,
        )
        self._firebase_repo.create_unlock_grant(
            request_id=request_id,
            granted=granted,
            reason=reason,
            mqtt_topic=f"droplock/{session.sector_id}/{session.locker_id}/cmd",
            mqtt_payload="OPEN",
        )

    def _publish_open(self, session: LockerSession) -> None:
        self._mqtt_client.publish_open(
            request_id=session.request_id,
            locker_id=session.locker_id,
            actor_uid=session.device_uid,
            booking_id=session.booking_id,
            token_id=session.token_id,
        )

    def _publish_close(self, session: LockerSession) -> None:
        self._mqtt_client.publish_close(
            request_id=session.request_id,
            locker_id=session.locker_id,
            actor_uid=session.device_uid,
            booking_id=session.booking_id,
            token_id=session.token_id,
        )

    def _fail_active_session(self, reason: str) -> None:
        if self._active_session is not None:
            self._append_booking_event("UNLOCK_DENIED", {"reason": reason})
        self._show_error_ui(reason)
        self._clear_active_session()

    def _clear_active_session(self, *, show_idle: bool = True) -> None:
        self._active_session = None
        self._active_validation = None
        self._active_open_sent_mono = None
        self._active_close_sent_mono = None
        self._active_cancel_close_pending = False
        if show_idle:
            self._show_idle_ui()

    def _notify_tamper_if_needed(self, *, locker_id: str, tamper_flag: bool, ts_ms: int) -> None:
        if not tamper_flag:
            self._tamper_alert_active[locker_id] = False
            return
        if self._tamper_alert_active.get(locker_id):
            return
        self._tamper_alert_active[locker_id] = True

        if self._email_notifier is None:
            logger.warning("Tamper detected for locker=%s but email notifier is unavailable", locker_id)
            return

        recipients = self._resolve_tamper_recipients()
        if not recipients:
            logger.warning(
                "Tamper detected for locker=%s but no admin recipients were resolved",
                locker_id,
            )
            return

        for recipient in recipients:
            self._email_notifier.send_tamper_alert_email_async(
                to_email=recipient["email"],
                recipient_name=recipient["name"],
                sector_id=self._device_context.sector_id,
                locker_id=locker_id,
                detected_at_ms=ts_ms,
            )

    def _resolve_tamper_recipients(self) -> list[dict[str, str]]:
        admin_uids: list[str] = []
        try:
            sector = self._firebase_repo.get_json(f"sectors/{self._device_context.sector_id}") or {}
            admin_uid_map = (
                sector.get("adminUids")
                or sector.get("localAdminUids")
                or {}
            )
            admin_uids = [uid for uid, enabled in admin_uid_map.items() if self._coerce_bool(enabled)]
        except Exception:
            logger.exception("Failed to read sector admin mapping sector_id=%s", self._device_context.sector_id)

        recipients: list[dict[str, str]] = []
        seen_emails: set[str] = set()
        for admin_uid in admin_uids:
            try:
                profile = self._firebase_repo.get_profile(admin_uid) or {}
            except Exception:
                logger.exception("Failed reading profile for local admin uid=%s", admin_uid)
                continue
            email = (profile.get("email") or "").strip().lower()
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            recipients.append({"email": email, "name": profile.get("displayName") or "Admin"})

        if recipients:
            return recipients

        # Fallback: no sector admin configured/resolved, notify super-admin(s).
        try:
            profiles = self._firebase_repo.get_json("profiles") or {}
        except Exception:
            logger.exception("Failed to read profiles fallback for tamper recipients")
            return []
        if not isinstance(profiles, dict):
            return []

        for profile in profiles.values():
            if not isinstance(profile, dict):
                continue
            role = str(profile.get("role") or "")
            if role != "superAdmin":
                continue
            email = str(profile.get("email") or "").strip().lower()
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            recipients.append({"email": email, "name": profile.get("displayName") or "Admin"})
        return recipients

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "y", "on"}:
                return True
            if lowered in {"0", "false", "no", "n", "off", ""}:
                return False
        return bool(value)

    def _append_booking_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._active_session is None:
            return
        self._firebase_repo.append_booking_event(
            booking_id=self._active_session.booking_id,
            event_type=event_type,
            actor_uid=self._device_context.device_uid,
            data=data,
        )

    def _purpose(self) -> str:
        if not self._active_validation or not self._active_validation.token_data:
            return ""
        return str(self._active_validation.token_data.get("purpose") or "")

    @staticmethod
    def _extract_ts_ms(payload: dict[str, Any] | None) -> int:
        if payload and isinstance(payload.get("ts"), int):
            return int(payload["ts"])
        return int(time.time() * 1000)

    def _ensure_scanner_started(self) -> None:
        if hasattr(self._scanner_input, "start"):
            self._scanner_input.start()

    def _ensure_mqtt_started(self) -> None:
        if hasattr(self._mqtt_client, "is_running") and self._mqtt_client.is_running():
            return
        self._mqtt_client.start()

    def _ensure_mqtt_started_with_retry(self) -> None:
        attempts = 0
        while True:
            try:
                self._ensure_mqtt_started()
                self._show_idle_ui()
                return
            except Exception as exc:
                attempts += 1
                max_attempts = self._mqtt_start_max_attempts
                if max_attempts is not None and max_attempts > 0 and attempts >= max_attempts:
                    logger.error(
                        "MQTT failed after %s attempts; giving up startup.",
                        attempts,
                        exc_info=True,
                    )
                    self._show_error_ui(
                        "Controller offline. Please check locker module network/power and restart this tablet."
                    )
                    raise RuntimeError(
                        f"MQTT startup failed after {attempts} attempts"
                    ) from exc

                logger.warning(
                    "MQTT not ready yet (attempt %s%s); retrying in %.1fs.",
                    attempts,
                    f"/{max_attempts}" if max_attempts is not None and max_attempts > 0 else "",
                    self._mqtt_start_retry_delay_s,
                    exc_info=True,
                )
                self._show_establishing_connection_ui()
                time.sleep(self._mqtt_start_retry_delay_s)

    def _log_event(self, name: str, **data: Any) -> None:
        if self._event_logger is not None:
            self._event_logger.log(name, **data)

    def _show_idle_ui(self) -> None:
        if self._ui_controller:
            self._ui_controller.show_idle()

    def _show_establishing_connection_ui(self) -> None:
        if self._ui_controller and hasattr(self._ui_controller, "show_establishing_connection"):
            self._ui_controller.show_establishing_connection()

    def _show_processing_request_ui(self, token_id: str) -> None:
        if not self._ui_controller:
            return
        if hasattr(self._ui_controller, "show_processing_request"):
            self._ui_controller.show_processing_request(token_id=token_id)
        else:
            self._ui_controller.show_validating(token_id=token_id)

    def _show_validating_ui(self, token_id: str) -> None:
        # Backward-compatible internal alias.
        self._show_processing_request_ui(token_id)

    def _show_denied_ui(self, token_id: str, reason: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_denied(token_id=token_id, reason=reason)

    def _show_unlocking_ui(self, session: LockerSession) -> None:
        if self._ui_controller:
            self._ui_controller.show_unlocking(session=session)

    def _show_locker_open_ui(self, session: LockerSession) -> None:
        if not self._ui_controller:
            return
        if hasattr(self._ui_controller, "show_locker_open"):
            self._ui_controller.show_locker_open(session=session)
        else:
            self._ui_controller.show_unlocking(session=session)

    def _show_weight_wait_ui(self, session: LockerSession, reason: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_weight_wait(session=session, reason=reason)

    def _show_signature_ui(self, session: LockerSession) -> None:
        if self._ui_controller:
            self._ui_controller.show_signature(session=session)

    def _show_closing_ui(self, session: LockerSession) -> None:
        if self._ui_controller:
            self._ui_controller.show_closing(session=session)

    def _show_completed_ui(self, session: LockerSession) -> None:
        if self._ui_controller:
            self._ui_controller.show_completed(session=session)

    def _show_busy_ui(self, token_id: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_busy(token_id=token_id)

    def _show_error_ui(self, reason: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_error(reason=reason)

    def _show_signature_failed_ui(self, reason: str | None) -> None:
        if self._ui_controller:
            self._ui_controller.show_signature_failed(reason=reason)

    def _show_close_blocked_ui(self, reasons: list[str]) -> None:
        if self._ui_controller:
            self._ui_controller.show_close_blocked(blocking_reasons=reasons)

    def _show_operation_cancelled_ui(self) -> None:
        if self._ui_controller and hasattr(self._ui_controller, "show_operation_cancelled"):
            self._ui_controller.show_operation_cancelled()
