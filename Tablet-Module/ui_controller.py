from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UIState:
    name: str
    title: str
    subtitle: str
    updated_at: datetime


class UIController:
    """Thin display layer; orchestrator owns all decisions."""

    def __init__(self, *, enable_tk: bool = False, fullscreen: bool = False) -> None:
        self._enable_tk = enable_tk
        self._fullscreen = fullscreen
        self._lock = threading.Lock()
        self._state = UIState("idle", "DropLock", "Scan QR to begin", datetime.now(timezone.utc))

    def show_idle(self) -> None:
        self._set_state("idle", "DropLock", "Scan QR to begin")

    def show_validating(self, *, token_id: str) -> None:
        self._set_state("validating", "Validating QR", f"Token: {token_id}")

    def show_denied(self, *, token_id: str, reason: str) -> None:
        self._set_state("denied", "Access denied", f"{reason} ({token_id})")

    def show_unlocking(self, *, session) -> None:
        self._set_state("unlocking", "Unlocking locker", f"Locker {session.locker_id}")

    def show_weight_wait(self, *, session, reason: str) -> None:
        self._set_state("waiting_weight", "Waiting for valid weight", reason)

    def show_signature(self, *, session) -> None:
        self._set_state("waiting_signature", "Signature required", f"Booking {session.booking_id}")

    def show_closing(self, *, session) -> None:
        self._set_state("closing", "Closing locker", f"Locker {session.locker_id}")

    def show_completed(self, *, session) -> None:
        self._set_state("completed", "Completed", f"Booking {session.booking_id}")

    def show_busy(self, *, token_id: str) -> None:
        self._set_state("busy", "System busy", f"Please wait ({token_id})")

    def show_error(self, *, reason: str) -> None:
        self._set_state("error", "Runtime error", reason)

    def show_signature_failed(self, *, reason: str | None) -> None:
        self._set_state("signature_failed", "Signature failed", reason or "Please retry")

    def show_close_blocked(self, *, blocking_reasons: list[str]) -> None:
        self._set_state("close_blocked", "Close blocked", ", ".join(blocking_reasons))

    def _set_state(self, name: str, title: str, subtitle: str) -> None:
        with self._lock:
            self._state = UIState(name, title, subtitle, datetime.now(timezone.utc))
        logger.info("UI state=%s title=%s subtitle=%s", name, title, subtitle)
