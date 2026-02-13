"""Locker and command actions with audit/alert side effects."""

from __future__ import annotations

import os

from admin_ops import assert_can_admin, assert_super_admin, get_profile
from alert_service import AlertService
from booking_audit import append_booking_event
from commands import push_admin_open_command
from locker_repo import append_locker_event, create_locker, delete_locker, get_locker, set_locker_state

ALERTS = AlertService()


def _notification_recipient(actor_uid: str) -> str | None:
    profile = get_profile(actor_uid) or {}
    return os.getenv("DROPLOCK_ALERT_RECIPIENT") or profile.get("email")


def admin_set_state(actor_uid: str, sector_id: str, locker_id: str, new_state: str) -> None:
    assert_can_admin(actor_uid, sector_id)

    locker = get_locker(sector_id, locker_id) or {}
    before_state = locker.get("state")
    booking_id = locker.get("activeBookingId")

    set_locker_state(sector_id, locker_id, new_state)
    after = get_locker(sector_id, locker_id) or {}

    append_locker_event(
        sector_id,
        locker_id,
        "MAINTENANCE_ON" if new_state == "MAINTENANCE" else "MAINTENANCE_OFF" if before_state == "MAINTENANCE" else "SET_STATE",
        actor_uid,
        {"state": before_state},
        {"state": new_state},
    )

    if booking_id:
        append_booking_event(
            booking_id=booking_id,
            event_type="STATUS_CHANGED",
            actor_uid=actor_uid,
            data={
                "source": "ADMIN_DASHBOARD",
                "lockerId": locker_id,
                "sectorId": sector_id,
                "before": before_state,
                "after": new_state,
            },
        )


def admin_request_open(actor_uid: str, sector_id: str, locker_id: str, reason: str = "") -> str:
    assert_can_admin(actor_uid, sector_id)

    locker = get_locker(sector_id, locker_id) or {}
    state = locker.get("state")
    booking_id = locker.get("activeBookingId")

    cmd_id = push_admin_open_command(sector_id, locker_id, actor_uid)
    append_locker_event(sector_id, locker_id, "ADMIN_OPEN", actor_uid, {"state": state}, {"cmdId": cmd_id})

    if booking_id:
        append_booking_event(
            booking_id=booking_id,
            event_type="UNLOCK_GRANTED",
            actor_uid=actor_uid,
            data={
                "source": "ADMIN_DASHBOARD",
                "cmdId": cmd_id,
                "lockerId": locker_id,
                "sectorId": sector_id,
                "forced": True,
                "reason": reason,
                "lockerState": state,
            },
        )
        alert_id = ALERTS.create_alert(
            alert_type="FAILED_OPEN",
            sector_id=sector_id,
            locker_id=locker_id,
            severity="HIGH",
            actor_uid=actor_uid,
            booking_id=booking_id,
        )
        if alert_id:
            ALERTS.maybe_send_email(
                recipient=_notification_recipient(actor_uid),
                alert_type="FAILED_OPEN",
                sector_id=sector_id,
                locker_id=locker_id,
                actor_uid=actor_uid,
                booking_id=booking_id,
            )
            append_locker_event(sector_id, locker_id, "FORCED_OPEN", actor_uid, locker, get_locker(sector_id, locker_id) or {})

    return cmd_id


def super_create_locker(actor_uid: str, sector_id: str, locker_id: str) -> None:
    assert_super_admin(actor_uid)
    create_locker(sector_id, locker_id)
    append_locker_event(sector_id, locker_id, "CREATE_LOCKER", actor_uid, None, get_locker(sector_id, locker_id) or {})


def super_delete_locker(actor_uid: str, sector_id: str, locker_id: str) -> None:
    assert_super_admin(actor_uid)
    before = get_locker(sector_id, locker_id) or {}
    delete_locker(sector_id, locker_id)
    append_locker_event(sector_id, locker_id, "DELETE_LOCKER", actor_uid, before, None)
