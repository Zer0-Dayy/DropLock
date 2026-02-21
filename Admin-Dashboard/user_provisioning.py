"""Provisioning and lifecycle operations for admin accounts."""

from __future__ import annotations

import secrets
import string
import time

from firebase_admin import auth, db

from admin_init import init_firebase
from smtp_service import SmtpService


SMTP = SmtpService()


def create_auth_user(email: str, temp_password: str) -> str:
    init_firebase()
    user_record = auth.create_user(email=email, password=temp_password)
    return user_record.uid


def _send_admin_welcome_email(email: str, display_name: str, sector_id: str) -> None:
    if not SMTP.enabled:
        return
    subject = "[DropLock] Admin profile created"
    body = (
        f"Hello {display_name or 'Admin'},\n\n"
        "Your DropLock admin profile is now active.\n"
        f"Assigned sector: {sector_id}\n"
        "Please sign in to the dashboard and change your temporary password immediately.\n"
    )
    SMTP.send_alert_email(to_email=email, subject=subject, body=body)


def provision_device(email: str, temp_password: str, sector_id: str, display_name: str) -> str:
    init_firebase()
    uid = create_auth_user(email, temp_password)
    now = int(time.time() * 1000)
    db.reference(f"profiles/{uid}").set(
        {
            "role": "device",
            "email": email,
            "displayName": display_name,
            "sectorId": sector_id,
            "status": "active",
            "createdAt": now,
        }
    )
    db.reference(f"sectors/{sector_id}/deviceUids/{uid}").set(True)
    return uid


def provision_admin(email: str, temp_password: str, sector_id: str, display_name: str) -> str:
    init_firebase()
    uid = create_auth_user(email, temp_password)
    now = int(time.time() * 1000)

    db.reference(f"profiles/{uid}").set(
        {
            "role": "admin",
            "email": email,
            "displayName": display_name,
            "sectorId": sector_id,
            "status": "active",
            "createdAt": now,
        }
    )
    db.reference(f"sectors/{sector_id}/adminUids/{uid}").set(True)
    _send_admin_welcome_email(email, display_name, sector_id)
    return uid


def list_admin_profiles() -> dict[str, dict]:
    init_firebase()
    profiles = db.reference("profiles").get() or {}
    return {uid: p for uid, p in profiles.items() if (p or {}).get("role") == "admin"}


def set_admin_status(uid: str, status: str) -> None:
    init_firebase()
    if status not in {"active", "disabled"}:
        raise ValueError("status must be active or disabled")
    db.reference(f"profiles/{uid}").update({"status": status})


def _generate_temp_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "@#_-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def reset_admin_password(uid: str) -> str:
    init_firebase()
    temp_password = _generate_temp_password()
    auth.update_user(uid, password=temp_password)
    return temp_password
