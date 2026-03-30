from __future__ import annotations

import logging
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

        self._running = False
        self._active_session: LockerSession | None = None
        self._active_validation: ValidationResult | None = None
        self._active_open_sent_mono: float | None = None
        self._active_close_sent_mono: float | None = None

        self._locker_door_closed: dict[str, bool] = {}

    def run_forever(self) -> None:
        self.start()
        try:
            while self._running:
                did_work = False
                did_work |= self._process_next_mqtt_message()
                did_work |= self._process_next_scan()
                did_work |= self._check_session_timeouts()
                if not did_work:
                    time.sleep(self._idle_sleep_s)
        finally:
            logger.info("Session orchestrator loop ended")

    def start(self) -> None:
        self._ensure_scanner_started()
        self._ensure_mqtt_started()
        self._running = True
        self._show_idle_ui()

    def stop(self) -> None:
        self._running = False

    def _process_next_scan(self) -> bool:
        scan = self._scanner_input.get_scan_nowait()
        if scan is None:
            return False

        token_id = getattr(scan, "normalized_text", None) or str(scan).strip()
        if not token_id:
            return True

        self._log_event("SCAN_RECEIVED", token_id=token_id)

        if self._active_session is not None:
            self._show_busy_ui(token_id)
            return True

        self._show_validating_ui(token_id)
        validation = self._validate_token(token_id)

        if not validation.allowed:
            reason = validation.reason or "DENIED"
            self._show_denied_ui(token_id, reason)
            self._log_event("QR_DENIED", token_id=token_id, reason=reason)
            return True

        session = self._build_session(token_id, validation)
        self._active_session = session
        self._active_validation = validation

        self._create_unlock_records(granted=True, reason="OK", session=session)
        self._show_unlocking_ui(session)
        self._publish_open(session)
        self._active_open_sent_mono = time.monotonic()
        return True

    def _process_next_mqtt_message(self) -> bool:
        raw_msg = self._mqtt_client.get_message(timeout=self._mqtt_poll_timeout_s)
        if raw_msg is None:
            return False

        event = self._controller_event_parser.parse(raw_msg)
        if event is None:
            return True

        self._route_event(event)
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
            self._firebase_repo.update_locker_heartbeat(
                sector_id=self._device_context.sector_id,
                locker_id=event.locker_id,
                heartbeat_at_ms=self._extract_ts_ms(event.payload),
            )
            return
        if event.event_type == ControllerEventType.TAMPER:
            self._firebase_repo.update_locker_tamper(
                sector_id=self._device_context.sector_id,
                locker_id=event.locker_id,
                tamper_flag=True,
                tamper_at_ms=self._extract_ts_ms(event.payload),
            )

    def _on_open_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        self._firebase_repo.mark_qr_token_used(token_id=self._active_session.token_id)
        self._append_booking_event("UNLOCK_GRANTED", {"requestId": self._active_session.request_id})

        purpose = self._purpose()
        if purpose == self.PURPOSE_COURIER_DROP:
            self._active_session = replace(self._active_session, phase=SessionPhase.WAITING_FOR_OTHER_GATES)
            self._show_weight_wait_ui(self._active_session, "Waiting for weight measurement")
            return

        self._active_session = replace(self._active_session, phase=SessionPhase.WAITING_FOR_SIGNATURE)
        self._show_signature_ui(self._active_session)
        self._capture_signature_and_close_if_ready()

    def _on_weight_measured(self, event: ControllerEvent) -> None:
        assert self._active_session is not None
        if self._purpose() != self.PURPOSE_COURIER_DROP:
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

        self._firebase_repo.update_booking_measured_weight(
            booking_id=self._active_session.booking_id,
            measured_weight_grams=measured,
        )
        self._append_booking_event(
            "WEIGHT_MEASURED",
            {"measuredWeightGrams": measured, "accepted": accepted, "reason": reason},
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
        self._close_gates._config.require_weight = self._purpose() == self.PURPOSE_COURIER_DROP

        result: CloseGateResult = self._close_gates.evaluate(
            self._active_session,
            door_closed=self._locker_door_closed.get(self._active_session.locker_id),
        )
        if not result.can_close:
            self._show_close_blocked_ui(result.blocking_reasons or [])
            return

        self._active_session = replace(self._active_session, phase=SessionPhase.CLOSING)
        self._show_closing_ui(self._active_session)
        self._publish_close(self._active_session)
        self._active_close_sent_mono = time.monotonic()

    def _on_close_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        purpose = self._purpose()
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

        if purpose == self.PURPOSE_COURIER_DROP:
            self._issue_pickup_token_and_notify(self._active_session.booking_id)

        self._show_completed_ui(self._active_session)
        self._clear_active_session()

    def _issue_pickup_token_and_notify(self, booking_id: str) -> None:
        if self._token_service is None:
            logger.warning("Token service missing; cannot issue USER_PICKUP token")
            return

        try:
            issued = self._token_service.issue_user_pickup_token(booking_id)
            booking = self._firebase_repo.get_booking(booking_id) or {}
            user_id = booking.get("userId")
            profile = self._firebase_repo.get_profile(user_id) if user_id else None
            email = (profile or {}).get("email")
            display_name = (profile or {}).get("displayName") or "customer"
            if email and self._email_notifier is not None:
                self._email_notifier.send_token_email_async(
                    to_email=email,
                    recipient_name=display_name,
                    booking_id=booking_id,
                    token_id=issued.token_id,
                    purpose=issued.purpose,
                )
        except Exception:
            logger.exception("Failed to issue pickup token / send notification booking_id=%s", booking_id)

    def _check_session_timeouts(self) -> bool:
        if self._active_session is None:
            return False

        now = time.monotonic()
        if self._active_session.phase == SessionPhase.UNLOCKING and self._active_open_sent_mono is not None:
            if now - self._active_open_sent_mono > self._open_ack_timeout_s:
                self._fail_active_session("OPEN_ACK_TIMEOUT")
                return True
        if self._active_session.phase == SessionPhase.CLOSING and self._active_close_sent_mono is not None:
            if now - self._active_close_sent_mono > self._close_ack_timeout_s:
                self._fail_active_session("CLOSE_ACK_TIMEOUT")
                return True
        return False

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

    def _clear_active_session(self) -> None:
        self._active_session = None
        self._active_validation = None
        self._active_open_sent_mono = None
        self._active_close_sent_mono = None
        self._show_idle_ui()

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

    def _log_event(self, name: str, **data: Any) -> None:
        if self._event_logger is not None:
            self._event_logger.log(name, **data)

    def _show_idle_ui(self) -> None:
        if self._ui_controller:
            self._ui_controller.show_idle()

    def _show_validating_ui(self, token_id: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_validating(token_id=token_id)

    def _show_denied_ui(self, token_id: str, reason: str) -> None:
        if self._ui_controller:
            self._ui_controller.show_denied(token_id=token_id, reason=reason)

    def _show_unlocking_ui(self, session: LockerSession) -> None:
        if self._ui_controller:
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
