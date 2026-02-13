"""Database operations for lockers, sectors, and locker events."""

from __future__ import annotations

import time
from typing import Any

from firebase_admin import db

from admin_init import init_firebase


def now_ms() -> int:
    return int(time.time() * 1000)


def load_all_sectors() -> dict[str, Any]:
    init_firebase()
    return db.reference("sectors").get() or {}


def load_lockers(sector_id: str) -> dict[str, Any]:
    init_firebase()
    return db.reference(f"lockers/{sector_id}").get() or {}


def get_locker(sector_id: str, locker_id: str) -> dict[str, Any] | None:
    init_firebase()
    return db.reference(f"lockers/{sector_id}/{locker_id}").get()


def set_locker_state(sector_id: str, locker_id: str, new_state: str) -> None:
    init_firebase()
    db.reference(f"lockers/{sector_id}/{locker_id}").update({"state": new_state, "lastChangedAt": now_ms()})


def create_locker(sector_id: str, locker_id: str) -> None:
    init_firebase()
    ref = db.reference(f"lockers/{sector_id}/{locker_id}")
    if ref.get() is not None:
        raise ValueError(f"Locker already exists: {sector_id}/{locker_id}")

    ts = now_ms()
    ref.set(
        {
            "state": "AVAILABLE",
            "activeBookingId": None,
            "lastChangedAt": ts,
            "lastHeartbeatAt": None,
            "tamper": {"flag": False, "lastAt": None},
        }
    )


def delete_locker(sector_id: str, locker_id: str) -> None:
    init_firebase()
    ref = db.reference(f"lockers/{sector_id}/{locker_id}")
    locker = ref.get()
    if locker is None:
        raise ValueError(f"Locker does not exist: {sector_id}/{locker_id}")
    if locker.get("activeBookingId"):
        raise PermissionError("Cannot delete: locker has an activeBookingId")
    ref.delete()


def append_locker_event(
    sector_id: str,
    locker_id: str,
    event_type: str,
    actor_uid: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    """Log event under /lockerEvents/{sector}/{locker}/{event}."""
    init_firebase()
    ref = db.reference(f"lockerEvents/{sector_id}/{locker_id}").push(
        {
            "type": event_type,
            "actorUid": actor_uid,
            "before": before,
            "after": after,
            "timestamp": now_ms(),
        }
    )
    return ref.key


def update_sector_config(sector_id: str, config: dict[str, Any]) -> None:
    init_firebase()
    db.reference(f"sectors/{sector_id}/config").update(config)
