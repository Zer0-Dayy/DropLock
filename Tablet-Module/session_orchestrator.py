from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

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

        # Latest known physical/controller state per locker
        self._locker_door_closed: dict[str, Optional[bool]] = {}
        self._locker_last_heartbeat_ms: dict[str, int] = {}
        self._locker_last_tamper_ms: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start scanner and MQTT client if not already started.
        """
        logger.info("Starting session orchestrator for sector=%s", self._device_context.sector_id)

        self._ensure_scanner_started()
        self._ensure_mqtt_started()

        self._running = True
        self._show_idle_ui()

    def stop(self) -> None:
        self._running = False
        logger.info("Stopping session orchestrator")

    def run_forever(self) -> None:
        """
        Main always-on loop.
        """
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
            logger.info("Session orchestrator stopped")

    def step_once(self) -> bool:
        """
        Single loop iteration for tests or integration harnesses.
        """
        did_work = False
        did_work |= self._process_next_mqtt_message()
        did_work |= self._process_next_scan()
        did_work |= self._check_session_timeouts()
        return did_work

    # ------------------------------------------------------------------
    # Main loop pieces
    # ------------------------------------------------------------------

    def _process_next_scan(self) -> bool:
        token_id = self._get_scan_nowait()
        if not token_id:
            return False

        logger.info("QR scanned token_id=%s", token_id)
        self._log_event("SCAN_RECEIVED", token_id=token_id)

        if self._active_session is not None:
            logger.warning(
                "Ignoring scan while session is active request_id=%s token_id=%s",
                self._active_session.request_id,
                token_id,
            )
            self._show_busy_ui(token_id)
            return True

        self._handle_scanned_token(token_id)
        return True
    def _process_next_mqtt_message(self) -> bool:
        raw_msg = self._get_mqtt_message(timeout=self._mqtt_poll_timeout_s)
        if raw_msg is None:
            return False

        event = self._parse_controller_event(raw_msg)
        if event is None:
            logger.warning("Ignoring unparsable controller message")
            return True

        self._route_controller_event(event)
        return True

    def _check_session_timeouts(self) -> bool:
        if self._active_session is None:
            return False

        now = time.monotonic()
        did_work = False

        if self._active_session.phase == SessionPhase.UNLOCKING:
            if self._session_age_s(now) > self._open_ack_timeout_s:
                logger.error(
                    "OPEN_ACK timeout request_id=%s",
                    self._active_session.request_id,
                )
                self._fail_active_session("OPEN_ACK_TIMEOUT")
                did_work = True

        elif self._active_session.phase == SessionPhase.CLOSING:
            if self._session_age_s(now, use_close_requested=True) > self._close_ack_timeout_s:
                logger.error(
                    "CLOSE_ACK timeout request_id=%s",
                    self._active_session.request_id,
                )
                self._fail_active_session("CLOSE_ACK_TIMEOUT")
                did_work = True

        return did_work

    # ------------------------------------------------------------------
    # Scan -> validation -> session creation
    # ------------------------------------------------------------------

    def _handle_scanned_token(self, token_id: str) -> None:
        self._show_validating_ui(token_id)

        try:
            validation = self._validate_token(token_id)
        except Exception:
            logger.exception("QR validation crashed token_id=%s", token_id)
            self._show_denied_ui(token_id, "VALIDATION_ERROR")
            self._log_event("VALIDATION_ERROR", token_id=token_id)
            return

        if not validation.allowed:
            logger.info(
                "QR denied token_id=%s reason=%s",
                token_id,
                validation.reason,
            )
            self._show_denied_ui(token_id, validation.reason)
            self._log_event(
                "QR_DENIED",
                token_id=token_id,
                reason=validation.reason,
            )
            return

        self._active_validation = validation
        session = self._build_session_from_validation(token_id, validation)
        self._active_session = session

        logger.info(
            "Session created request_id=%s purpose=%s locker_id=%s booking_id=%s",
            session.request_id,
            self._purpose(),
            session.locker_id,
            session.booking_id,
        )
        self._log_event(
            "SESSION_CREATED",
            request_id=session.request_id,
            token_id=session.token_id,
            booking_id=session.booking_id,
            locker_id=session.locker_id,
            purpose=self._purpose(),
        )

        self._show_unlocking_ui(session)
        self._publish_open(session)

    def _build_session_from_validation(
        self,
        token_id: str,
        validation: ValidationResult,
    ) -> LockerSession:
        booking_data = validation.booking_data or {}
        token_data = validation.token_data or {}
        now = self._now_dt()

        return LockerSession(
            request_id=self._make_request_id(),
            token_id=token_id,
            booking_id=str(token_data.get("bookingId") or booking_data.get("bookingId") or ""),
            locker_id=str(token_data.get("lockerId") or booking_data.get("lockerId") or ""),
            sector_id=str(token_data.get("sectorId") or booking_data.get("sectorId") or self._device_context.sector_id),
            device_uid=self._device_context.device_uid,
            phase=SessionPhase.UNLOCKING,
            created_at=now,
            opened_at=None,
            signature_captured_at=None,
            close_requested_at=None,
            completed_at=None,
            signature_path=None,
            weight_expected_grams=booking_data.get("expectedWeightGrams"),
            weight_measured_grams=None,
            weight_accepted=None,
        )
    # ------------------------------------------------------------------
    # Event routing
    # ------------------------------------------------------------------

    def _route_controller_event(self, event: ControllerEvent) -> None:
        logger.info(
            "Routing controller event type=%s locker_id=%s request_id=%s",
            event.event_type,
            event.locker_id,
            event.request_id,
        )
        self._log_event(
            "CONTROLLER_EVENT",
            event_type=event.event_type.value,
            locker_id=event.locker_id,
            request_id=event.request_id,
            payload=event.payload,
        )

        if event.event_type in {ControllerEventType.HEARTBEAT, ControllerEventType.TAMPER}:
            self._handle_background_event(event)
            return

        if self._active_session is None:
            # Sessionless door open/close etc. are treated as background telemetry.
            self._handle_background_event(event)
            return

        if event.locker_id != self._active_session.locker_id:
            logger.info(
                "Ignoring event for different locker active=%s incoming=%s",
                self._active_session.locker_id,
                event.locker_id,
            )
            return

        if event.request_id and event.request_id != self._active_session.request_id:
            logger.info(
                "Ignoring event for different request_id active=%s incoming=%s",
                self._active_session.request_id,
                event.request_id,
            )
            return

        self._handle_session_event(event)

    # ------------------------------------------------------------------
    # Background events
    # ------------------------------------------------------------------

    def _handle_background_event(self, event: ControllerEvent) -> None:
        if event.event_type == ControllerEventType.HEARTBEAT:
            self._handle_heartbeat(event)
            return

        if event.event_type == ControllerEventType.TAMPER:
            self._handle_tamper(event)
            return

        if event.event_type == ControllerEventType.DOOR_OPEN:
            self._locker_door_closed[event.locker_id] = False
            logger.info("Background DOOR_OPEN locker_id=%s", event.locker_id)
            return

        if event.event_type == ControllerEventType.DOOR_CLOSED:
            self._locker_door_closed[event.locker_id] = True
            logger.info("Background DOOR_CLOSED locker_id=%s", event.locker_id)
            return

        logger.info("Unhandled background event type=%s", event.event_type.value)

    def _handle_heartbeat(self, event: ControllerEvent) -> None:
        ts = self._extract_ts_ms(event.payload)
        self._locker_last_heartbeat_ms[event.locker_id] = ts

        door_closed = event.payload.get("doorClosed")
        if isinstance(door_closed, bool):
            self._locker_door_closed[event.locker_id] = door_closed

        logger.info(
            "HEARTBEAT locker_id=%s door_closed=%s",
            event.locker_id,
            self._locker_door_closed.get(event.locker_id),
        )

        self._repo_try_call(
            "update_locker_heartbeat",
            sector_id=self._device_context.sector_id,
            locker_id=event.locker_id,
            heartbeat_at_ms=ts,
        )

    def _handle_tamper(self, event: ControllerEvent) -> None:
        ts = self._extract_ts_ms(event.payload)
        self._locker_last_tamper_ms[event.locker_id] = ts

        logger.warning("TAMPER locker_id=%s payload=%s", event.locker_id, event.payload)

        self._repo_try_call(
            "update_locker_tamper",
            sector_id=self._device_context.sector_id,
            locker_id=event.locker_id,
            tamper_flag=True,
            tamper_at_ms=ts,
        )
    # ------------------------------------------------------------------
    # Session events
    # ------------------------------------------------------------------

    def _handle_session_event(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        if event.event_type == ControllerEventType.OPEN_ACK:
            self._on_open_ack(event)
            return

        if event.event_type == ControllerEventType.OPEN_DENIED:
            self._on_open_denied(event)
            return

        if event.event_type == ControllerEventType.WEIGHT_MEASURED:
            self._on_weight_measured(event)
            return

        if event.event_type == ControllerEventType.DOOR_OPEN:
            self._locker_door_closed[event.locker_id] = False
            logger.info("Session DOOR_OPEN locker_id=%s", event.locker_id)
            return

        if event.event_type == ControllerEventType.DOOR_CLOSED:
            self._locker_door_closed[event.locker_id] = True
            logger.info("Session DOOR_CLOSED locker_id=%s", event.locker_id)
            return

        if event.event_type == ControllerEventType.CLOSE_ACK:
            self._on_close_ack(event)
            return

        logger.info("Unhandled session event type=%s", event.event_type.value)

    def _on_open_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        ok = bool(event.payload.get("ok", True))
        if not ok:
            reason = str(event.payload.get("detail") or "OPEN_ACK_NOT_OK")
            logger.warning("OPEN_ACK not ok reason=%s", reason)
            self._fail_active_session(reason)
            return

        now = self._now_dt()
        self._active_session = replace(
            self._active_session,
            opened_at=now,
            phase=(
                SessionPhase.WAITING_FOR_OTHER_GATES
                if self._purpose() == self.PURPOSE_COURIER_DROP
                else SessionPhase.WAITING_FOR_SIGNATURE
            ),
        )

        logger.info(
            "OPEN granted request_id=%s phase=%s",
            self._active_session.request_id,
            self._active_session.phase.value,
        )

        self._mark_token_used(self._active_session.token_id)
        self._append_booking_event(
            booking_id=self._active_session.booking_id,
            event_type="UNLOCK_GRANTED",
            data={"requestId": self._active_session.request_id},
        )

        # USER_PICKUP goes directly to signature.
        if self._purpose() == self.PURPOSE_USER_PICKUP:
            self._show_signature_ui(self._active_session)
            self._capture_signature_and_maybe_close()

    def _on_open_denied(self, event: ControllerEvent) -> None:
        reason = str(event.payload.get("detail") or "OPEN_DENIED")
        logger.warning("OPEN denied reason=%s", reason)
        self._fail_active_session(reason)

    def _on_weight_measured(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        if self._purpose() != self.PURPOSE_COURIER_DROP:
            logger.info("Ignoring WEIGHT_MEASURED for non-courier purpose")
            return

        measured = event.payload.get("weightGrams")
        if measured is None:
            logger.warning("WEIGHT_MEASURED missing weightGrams")
            return

        try:
            measured_int = int(measured)
        except (TypeError, ValueError):
            logger.warning("Invalid weightGrams=%r", measured)
            return

        self._active_session = replace(
            self._active_session,
            weight_measured_grams=measured_int,
        )

        accepted, reason = self._close_gates.evaluate_weight_only(self._active_session)
        self._active_session = replace(
            self._active_session,
            weight_accepted=accepted,
        )

        logger.info(
            "Weight measured expected=%s measured=%s accepted=%s reason=%s",
            self._active_session.weight_expected_grams,
            self._active_session.weight_measured_grams,
            accepted,
            reason,
        )

        self._append_booking_event(
            booking_id=self._active_session.booking_id,
            event_type="WEIGHT_MEASURED",
            data={
                "requestId": self._active_session.request_id,
                "measuredWeightGrams": measured_int,
                "accepted": accepted,
                "reason": reason,
            },
        )

        self._repo_try_call(
            "update_booking_measured_weight",
            booking_id=self._active_session.booking_id,
            measured_weight_grams=measured_int,
        )

        if not accepted:
            # Remain in WAITING_FOR_OTHER_GATES until weight becomes valid.
            self._show_weight_wait_ui(self._active_session, reason)
            return

        logger.info("Weight accepted request_id=%s", self._active_session.request_id)
        self._show_signature_ui(self._active_session)
        self._active_session = replace(
            self._active_session,
            phase=SessionPhase.WAITING_FOR_SIGNATURE,
        )
        self._capture_signature_and_maybe_close()

    def _on_close_ack(self, event: ControllerEvent) -> None:
        assert self._active_session is not None

        ok = bool(event.payload.get("ok", True))
        if not ok:
            reason = str(event.payload.get("detail") or "CLOSE_ACK_NOT_OK")
            logger.warning("CLOSE_ACK not ok reason=%s", reason)
            self._fail_active_session(reason)
            return

        self._locker_door_closed[self._active_session.locker_id] = True
        now = self._now_dt()

        finished_session = replace(
            self._active_session,
            completed_at=now,
            phase=SessionPhase.COMPLETED,
        )
        self._active_session = finished_session

        logger.info(
            "Session completed request_id=%s booking_id=%s purpose=%s",
            finished_session.request_id,
            finished_session.booking_id,
            self._purpose(),
        )

        self._append_booking_event(
            booking_id=finished_session.booking_id,
            event_type="STATUS_CHANGED",
            data={
                "requestId": finished_session.request_id,
                "phase": finished_session.phase.value,
                "purpose": self._purpose(),
            },
        )

        self._repo_try_call(
            "update_locker_state_post_session",
            sector_id=finished_session.sector_id,
            locker_id=finished_session.locker_id,
            booking_id=finished_session.booking_id,
            purpose=self._purpose(),
        )

        self._repo_try_call(
            "update_booking_status_post_session",
            booking_id=finished_session.booking_id,
            purpose=self._purpose(),
        )

        if self._purpose() == self.PURPOSE_COURIER_DROP:
            self._notify_user_drop_completed(finished_session.booking_id)

        self._show_completed_ui(finished_session)
        self._clear_active_session()
    # ------------------------------------------------------------------
    # Signature + close
    # ------------------------------------------------------------------

    def _capture_signature_and_maybe_close(self) -> None:
        assert self._active_session is not None

        signer_role = self._signature_role_for_purpose()

        result: SignatureResult = self._signature_capture.capture_signature(
            session=self._active_session,
            signer_role=signer_role,
            prompt_text=self._signature_prompt_for_purpose(),
        )

        if not result.captured or not result.valid:
            logger.warning(
                "Signature capture failed valid=%s reason=%s",
                result.valid,
                result.validation_reason,
            )
            self._show_signature_failed_ui(result.validation_reason)
            return

        self._active_session = replace(
            self._active_session,
            signature_captured_at=result.signed_at,
            signature_path=result.local_file_path,
            phase=SessionPhase.READY_TO_CLOSE,
        )

        logger.info(
            "Signature captured request_id=%s path=%s",
            self._active_session.request_id,
            self._active_session.signature_path,
        )

        self._append_booking_event(
            booking_id=self._active_session.booking_id,
            event_type="STATUS_CHANGED",
            data={
                "requestId": self._active_session.request_id,
                "signatureCaptured": True,
                "signerRole": result.signer_role,
                "signaturePath": result.local_file_path,
            },
        )

        self._attempt_close_if_ready()

    def _attempt_close_if_ready(self) -> None:
        assert self._active_session is not None

        require_weight = (self._purpose() == self.PURPOSE_COURIER_DROP)
        self._set_close_gates_weight_requirement(require_weight)

        result: CloseGateResult = self._close_gates.evaluate(
            self._active_session,
            door_closed=self._locker_door_closed.get(self._active_session.locker_id),
        )

        if not result.can_close:
            logger.info(
                "Close blocked request_id=%s reasons=%s",
                self._active_session.request_id,
                result.blocking_reasons,
            )
            self._show_close_blocked_ui(result.blocking_reasons)
            return

        now = self._now_dt()
        self._active_session = replace(
            self._active_session,
            close_requested_at=now,
            phase=SessionPhase.CLOSING,
        )

        self._show_closing_ui(self._active_session)
        self._publish_close(self._active_session)

    # ------------------------------------------------------------------
    # Publish helpers
    # ------------------------------------------------------------------

    def _publish_open(self, session: LockerSession) -> None:
        logger.info(
            "Publishing OPEN request_id=%s locker_id=%s booking_id=%s",
            session.request_id,
            session.locker_id,
            session.booking_id,
        )
        self._mqtt_client.publish_open(
            request_id=session.request_id,
            locker_id=session.locker_id,
            actor_uid=session.device_uid,
            booking_id=session.booking_id,
            token_id=session.token_id,
        )

    def _publish_close(self, session: LockerSession) -> None:
        logger.info(
            "Publishing CLOSE request_id=%s locker_id=%s booking_id=%s",
            session.request_id,
            session.locker_id,
            session.booking_id,
        )
        self._mqtt_client.publish_close(
            request_id=session.request_id,
            locker_id=session.locker_id,
            actor_uid=session.device_uid,
            booking_id=session.booking_id,
            token_id=session.token_id,
        )

    # ------------------------------------------------------------------
    # Failure / cleanup
    # ------------------------------------------------------------------

    def _fail_active_session(self, reason: str) -> None:
        if self._active_session is not None:
            self._append_booking_event(
                booking_id=self._active_session.booking_id,
                event_type="UNLOCK_DENIED",
                data={
                    "requestId": self._active_session.request_id,
                    "reason": reason,
                },
            )

        logger.error("Active session failed reason=%s", reason)
        self._show_error_ui(reason)
        self._clear_active_session()

    def _clear_active_session(self) -> None:
        self._active_session = None
        self._active_validation = None
        self._show_idle_ui()
    # ------------------------------------------------------------------
    # Validator / parser / repo adapters
    # ------------------------------------------------------------------

    def _validate_token(self, token_id: str) -> ValidationResult:
        """
        Flexible adapter so small interface differences do not break orchestration.
        """
        validator = self._access_validator

        if hasattr(validator, "validate"):
            try:
                return validator.validate(token_id, self._device_context.sector_id)
            except TypeError:
                pass

            try:
                return validator.validate(token_id=token_id, device_context=self._device_context)
            except TypeError:
                pass

            try:
                return validator.validate(token_id=token_id, sector_id=self._device_context.sector_id)
            except TypeError:
                pass

        if hasattr(validator, "validate_token"):
            try:
                return validator.validate_token(token_id=token_id, device_context=self._device_context)
            except TypeError:
                pass

            try:
                return validator.validate_token(token_id, self._device_context.sector_id)
            except TypeError:
                pass

        raise RuntimeError("Could not call access validator with known method signatures")

    def _parse_controller_event(self, raw_msg: Any) -> ControllerEvent | None:
        parser = self._controller_event_parser

        if hasattr(parser, "parse"):
            return parser.parse(raw_msg)

        raise RuntimeError("Controller event parser does not expose parse(raw_msg)")

    def _mark_token_used(self, token_id: str) -> None:
        logger.info("Marking token used token_id=%s", token_id)
        self._firebase_repo.mark_qr_token_used(token_id=token_id, used_at_ms=self._now_ms())

    def _append_booking_event(
        self,
        *,
        booking_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        self._repo_try_call(
            "append_booking_event",
            booking_id=booking_id,
            event_type=event_type,
            actor_uid=self._device_context.device_uid,
            data=data,
        )

    def _repo_try_call(self, method_name: str, **kwargs: Any) -> Any:
        method = getattr(self._firebase_repo, method_name, None)
        if method is None:
            return None

        try:
            return method(**kwargs)
        except Exception:
            logger.exception("Repo helper failed method=%s kwargs=%s", method_name, kwargs)
            return None

    # ------------------------------------------------------------------
    # Notification hook
    # ------------------------------------------------------------------

    def _notify_user_drop_completed(self, booking_id: str) -> None:
        """
        Hook for later SMTP integration.

        Expected data path:
        qrTokens -> bookingId -> bookings/{bookingId}/userId -> profiles/{userId}/email
        """
        if self._email_notifier is None:
            logger.info("No email notifier configured; skipping courier completion email")
            return

        try:
            booking = self._firebase_repo.get_booking(booking_id)
            if not booking:
                logger.warning("Cannot notify user; booking missing booking_id=%s", booking_id)
                return

            user_id = booking.get("userId")
            if not user_id:
                logger.warning("Cannot notify user; booking missing userId booking_id=%s", booking_id)
                return

            profile = self._firebase_repo.get_profile(user_id)
            if not profile:
                logger.warning("Cannot notify user; profile missing user_id=%s", user_id)
                return

            target_email = profile.get("email")
            if not target_email:
                logger.warning("Cannot notify user; profile missing email user_id=%s", user_id)
                return

            if hasattr(self._email_notifier, "notify_drop_completed"):
                self._email_notifier.notify_drop_completed(
                    booking_id=booking_id,
                    target_email=target_email,
                    profile=profile,
                    booking=booking,
                )
            elif hasattr(self._email_notifier, "send_drop_completed_email"):
                self._email_notifier.send_drop_completed_email(
                    booking_id=booking_id,
                    target_email=target_email,
                    profile=profile,
                    booking=booking,
                )
            else:
                logger.warning("Email notifier configured but no known notify method found")

        except Exception:
            logger.exception("Courier completion email failed booking_id=%s", booking_id)
    # ------------------------------------------------------------------
    # Internal utility
    # ------------------------------------------------------------------

    def _get_scan_nowait(self) -> Optional[str]:
        scanner = self._scanner_input

        if hasattr(scanner, "get_scan_nowait"):
            return scanner.get_scan_nowait()

        if hasattr(scanner, "get_scan"):
            try:
                return scanner.get_scan(timeout=0.0)
            except TypeError:
                return scanner.get_scan()

        raise RuntimeError("Scanner input does not expose a known scan API")

    def _get_mqtt_message(self, timeout: float) -> Any | None:
        mqtt_client = self._mqtt_client

        if hasattr(mqtt_client, "get_message"):
            return mqtt_client.get_message(timeout=timeout)

        raise RuntimeError("MQTT client does not expose get_message(timeout=...)")

    def _ensure_scanner_started(self) -> None:
        if hasattr(self._scanner_input, "start"):
            try:
                self._scanner_input.start()
            except RuntimeError:
                # already started
                pass

    def _ensure_mqtt_started(self) -> None:
        if hasattr(self._mqtt_client, "is_running") and self._mqtt_client.is_running():
            return

        self._mqtt_client.start()

    def _make_request_id(self) -> str:
        return f"req_{uuid.uuid4().hex[:10]}"

    def _purpose(self) -> str:
        if self._active_validation is None or not self._active_validation.token_data:
            return ""
        return str(self._active_validation.token_data.get("purpose") or "")

    def _signature_role_for_purpose(self) -> str:
        if self._purpose() == self.PURPOSE_COURIER_DROP:
            return "courier"
        if self._purpose() == self.PURPOSE_USER_PICKUP:
            return "user"
        return "unknown"

    def _signature_prompt_for_purpose(self) -> str:
        if self._purpose() == self.PURPOSE_COURIER_DROP:
            return "Courier signature required after valid weight confirmation."
        if self._purpose() == self.PURPOSE_USER_PICKUP:
            return "User signature required before closing the locker."
        return "Signature required before closing the locker."

    def _set_close_gates_weight_requirement(self, require_weight: bool) -> None:
        """
        Adjust weight requirement dynamically by purpose without redesigning CloseGates.
        """
        cfg = getattr(self._close_gates, "_config", None)
        if cfg is None:
            return

        # mutate config intentionally; orchestrator owns usage policy
        try:
            cfg.require_weight = require_weight
        except Exception:
            pass

    def _session_age_s(self, now_monotonic: float, use_close_requested: bool = False) -> float:
        assert self._active_session is not None
        anchor = self._active_session.created_at

        if use_close_requested and self._active_session.close_requested_at is not None:
            anchor = self._active_session.close_requested_at

        return now_monotonic - anchor.timestamp()

    @staticmethod
    def _extract_ts_ms(payload: dict[str, Any]) -> int:
        ts = payload.get("ts")
        if isinstance(ts, int):
            return ts
        return int(time.time() * 1000)

    @staticmethod
    def _now_dt() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _log_event(self, name: str, **data: Any) -> None:
        if self._event_logger is None:
            return

        if hasattr(self._event_logger, "log"):
            try:
                self._event_logger.log(name=name, **data)
            except Exception:
                logger.exception("event_logger.log failed")
                return

        if hasattr(self._event_logger, "info"):
            try:
                self._event_logger.info(name, extra=data)
            except Exception:
                logger.exception("event_logger.info failed")

    # ------------------------------------------------------------------
    # Optional UI hooks
    # ------------------------------------------------------------------

    def _show_idle_ui(self) -> None:
        self._ui_try("show_idle")

    def _show_validating_ui(self, token_id: str) -> None:
        self._ui_try("show_validating", token_id=token_id)

    def _show_unlocking_ui(self, session: LockerSession) -> None:
        self._ui_try("show_unlocking", session=session)

    def _show_weight_wait_ui(self, session: LockerSession, reason: str) -> None:
        self._ui_try("show_weight_wait", session=session, reason=reason)

    def _show_signature_ui(self, session: LockerSession) -> None:
        self._ui_try("show_signature", session=session)

    def _show_closing_ui(self, session: LockerSession) -> None:
        self._ui_try("show_closing", session=session)

    def _show_completed_ui(self, session: LockerSession) -> None:
        self._ui_try("show_completed", session=session)

    def _show_denied_ui(self, token_id: str, reason: str) -> None:
        self._ui_try("show_denied", token_id=token_id, reason=reason)

    def _show_error_ui(self, reason: str) -> None:
        self._ui_try("show_error", reason=reason)

    def _show_close_blocked_ui(self, blocking_reasons: list[str]) -> None:
        self._ui_try("show_close_blocked", blocking_reasons=blocking_reasons)

    def _show_signature_failed_ui(self, reason: str) -> None:
        self._ui_try("show_signature_failed", reason=reason)

    def _show_busy_ui(self, token_id: str) -> None:
        self._ui_try("show_busy", token_id=token_id)

    def _ui_try(self, method_name: str, **kwargs: Any) -> None:
        if self._ui_controller is None:
            return

        method = getattr(self._ui_controller, method_name, None)
        if method is None:
            return

        try:
            method(**kwargs)
        except Exception:
            logger.exception("UI method failed method=%s kwargs=%s", method_name, kwargs)
