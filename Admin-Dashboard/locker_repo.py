# locker_repo.py
import time
from admin_init import init_firebase
from firebase_admin import db
from typing import Optional

def now_ms() -> int:
    return int(time.time() * 1000)

def get_locker(sector_id: str, locker_id: str) -> dict | None:
    init_firebase()
    return db.reference(f"lockers/{sector_id}/{locker_id}").get()

def set_locker_state(sector_id: str, locker_id: str, new_state: str) -> None:
    init_firebase()
    ref = db.reference(f"lockers/{sector_id}/{locker_id}")
    ref.update({
        "state": new_state,
        "lastChangedAt": now_ms(),
    })

def create_locker(sector_id: str, locker_id: str) -> None:
    init_firebase()
    ref = db.reference(f"lockers/{sector_id}/{locker_id}")

    existing = ref.get()
    if existing is not None:
        raise ValueError(f"Locker already exists: {sector_id}/{locker_id}")

    ts = now_ms()
    ref.set({
        "state": "AVAILABLE",
        "activeBookingId": None,
        "lastChangedAt": ts,
        "lastHeartbeatAt": None,   # device will fill later
        "tamper": {
            "flag": False,
            "lastAt": None
        }
    })

def delete_locker(sector_id: str, locker_id: str) -> None:
    init_firebase()
    ref = db.reference(f"lockers/{sector_id}/{locker_id}")

    locker = ref.get()
    if locker is None:
        raise ValueError(f"Locker does not exist: {sector_id}/{locker_id}")

    if locker.get("activeBookingId"):
        raise PermissionError("Cannot delete: locker has an activeBookingId")

    ref.delete()
