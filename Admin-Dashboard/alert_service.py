"""Alert node operations and notification orchestration."""

from __future__ import annotations

import logging
import time
from typing import Any

from firebase_admin import db

from admin_init import init_firebase
from smtp_service import SmtpService

LOGGER = logging.getLogger(__name__)


class AlertService:
    """Manage /alerts entries without altering locker source of truth."""

    def __init__(self, smtp_service: SmtpService | None = None):
        self.smtp_service = smtp_service or SmtpService()

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    def list_alerts(self) -> dict[str, dict[str, Any]]:
        init_firebase()
        return db.reference("alerts").get() or {}

    def list_open_alerts_for_locker(self, sector_id: str, locker_id: str, alert_type: str) -> dict[str, dict[str, Any]]:
        alerts = self.list_alerts()
        out: dict[str, dict[str, Any]] = {}
        for alert_id, data in alerts.items():
            safe = data or {}
            if (
                safe.get("sectorId") == sector_id
                and safe.get("lockerId") == locker_id
                and safe.get("type") == alert_type
                and safe.get("status") in {"OPEN", "ACKED"}
            ):
                out[alert_id] = safe
        return out

    def create_alert(
        self,
        *,
        alert_type: str,
        sector_id: str,
        locker_id: str,
        severity: str,
        actor_uid: str | None = None,
        booking_id: str | None = None,
    ) -> str | None:
        """Create an alert only if an open/acked alert of same type doesn't already exist."""
        init_firebase()
        existing = self.list_open_alerts_for_locker(sector_id, locker_id, alert_type)
        if existing:
            return None

        payload: dict[str, Any] = {
            "type": alert_type,
            "sectorId": sector_id,
            "lockerId": locker_id,
            "severity": severity,
            "status": "OPEN",
            "createdAt": self.now_ms(),
            "ackedByUid": None,
        }
        if actor_uid:
            payload["actorUid"] = actor_uid
        if booking_id:
            payload["bookingId"] = booking_id

        ref = db.reference("alerts").push(payload)
        LOGGER.info("Created alert %s type=%s sector=%s locker=%s", ref.key, alert_type, sector_id, locker_id)
        return ref.key

    def update_status(self, alert_id: str, status: str, actor_uid: str | None = None) -> None:
        init_firebase()
        payload: dict[str, Any] = {"status": status}
        if status == "ACKED" and actor_uid:
            payload["ackedByUid"] = actor_uid
        db.reference(f"alerts/{alert_id}").update(payload)

    def maybe_send_email(self, *, recipient: str | None, alert_type: str, sector_id: str, locker_id: str, actor_uid: str | None, booking_id: str | None) -> None:
        if not recipient:
            LOGGER.warning("No recipient provided; skipping email notification")
            return

        subject = f"[DropLock Alert] {alert_type} - {sector_id}/{locker_id}"
        body = (
            f"Alert Type: {alert_type}\n"
            f"Sector: {sector_id}\n"
            f"Locker: {locker_id}\n"
            f"Timestamp: {self.now_ms()}\n"
            f"Actor: {actor_uid or 'N/A'}\n"
            f"Booking ID: {booking_id or 'N/A'}\n"
        )
        self.smtp_service.send_alert_email(to_email=recipient, subject=subject, body=body)
