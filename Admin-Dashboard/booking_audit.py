# booking_audit.py
import time
from admin_init import init_firebase
from firebase_admin import db

def now_ms() -> int:
    return int(time.time() * 1000)

def append_booking_event(booking_id: str, event_type: str, actor_uid: str, data: dict) -> None:
    init_firebase()
    db.reference(f"bookingEvents/{booking_id}").push({
        "type": event_type,
        "ts": now_ms(),
        "actorUid": actor_uid,
        "data": data or {},
    })
