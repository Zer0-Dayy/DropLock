# commands.py
import time
from admin_init import init_firebase
from firebase_admin import db

def now_ms() -> int:
    return int(time.time() * 1000)

def push_admin_open_command(sector_id: str, locker_id: str, actor_uid: str) -> str:
    init_firebase()
    ref = db.reference(f"adminCommands/{sector_id}/{locker_id}").push({
        "cmd": "OPEN",
        "actorUid": actor_uid,
        "ts": now_ms(),
    })
    return ref.key  # push id
