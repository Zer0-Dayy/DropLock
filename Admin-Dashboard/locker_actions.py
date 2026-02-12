# locker_actions.py
from admin_ops import assert_can_admin
from locker_repo import get_locker, set_locker_state
from booking_audit import append_booking_event
from commands import push_admin_open_command
from admin_ops import assert_super_admin
from locker_repo import create_locker, delete_locker

def admin_set_state(actor_uid: str, sector_id: str, locker_id: str, new_state: str) -> None:
    assert_can_admin(actor_uid, sector_id)

    locker = get_locker(sector_id, locker_id) or {}
    before = locker.get("state")
    booking_id = locker.get("activeBookingId")

    set_locker_state(sector_id, locker_id, new_state)

    # Log only when booked
    if booking_id:
        append_booking_event(
            booking_id=booking_id,
            event_type="STATUS_CHANGED",
            actor_uid=actor_uid,
            data={
                "source": "ADMIN_DASHBOARD",
                "lockerId": locker_id,
                "sectorId": sector_id,
                "before": before,
                "after": new_state,
            },
        )

def admin_request_open(actor_uid: str, sector_id: str, locker_id: str, reason: str = "") -> str:
    assert_can_admin(actor_uid, sector_id)

    locker = get_locker(sector_id, locker_id) or {}
    state = locker.get("state")
    booking_id = locker.get("activeBookingId")

    cmd_id = push_admin_open_command(sector_id, locker_id, actor_uid)

    # Log only when booked
    if booking_id:
        forced = bool(booking_id)  # by your MVP rule: booked => we care
        # If you want "forced" to mean specifically RESERVED/OCCUPIED, use:
        # forced = state in ("RESERVED", "OCCUPIED")
        append_booking_event(
            booking_id=booking_id,
            event_type="UNLOCK_GRANTED",
            actor_uid=actor_uid,
            data={
                "source": "ADMIN_DASHBOARD",
                "cmdId": cmd_id,
                "lockerId": locker_id,
                "sectorId": sector_id,
                "forced": forced,
                "reason": reason,
                "lockerState": state,
            },
        )

    return cmd_id

def super_create_locker(actor_uid: str, sector_id: str, locker_id: str) -> None:
    assert_super_admin(actor_uid)
    create_locker(sector_id, locker_id)

def super_delete_locker(actor_uid: str, sector_id: str, locker_id: str) -> None:
    assert_super_admin(actor_uid)
    delete_locker(sector_id, locker_id)
