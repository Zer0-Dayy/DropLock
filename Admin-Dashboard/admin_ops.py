"""Admin profile and authorization helpers."""

from __future__ import annotations

import logging

from firebase_admin import db

from admin_init import init_firebase

LOGGER = logging.getLogger(__name__)


def get_profile(uid: str) -> dict | None:
    """Load profile data by uid from RTDB."""
    init_firebase()
    ref = f"profiles/{uid}"
    data = db.reference(ref).get()
    if data is None:
        LOGGER.warning("No profile found at %s", ref)
    return data


def assert_can_admin(uid: str, sector_id: str | None = None) -> dict:
    """Ensure user is active superAdmin/admin for the target sector."""
    profile = get_profile(uid)
    if not profile:
        raise PermissionError("Invalid Admin UID")
    if profile.get("status") != "active":
        raise PermissionError("Inactive profile")

    role = profile.get("role")
    if role == "superAdmin":
        return profile
    if role == "admin":
        if sector_id is None:
            raise PermissionError("Sector ID is required for admins")
        if profile.get("sectorId") != sector_id:
            raise PermissionError("Admin not assigned to this sector")
        return profile

    raise PermissionError("Not an admin")


def assert_super_admin(uid: str) -> dict:
    """Ensure user is active superAdmin."""
    profile = get_profile(uid)
    if not profile:
        raise PermissionError("No profile")
    if profile.get("status") != "active":
        raise PermissionError("Profile disabled")
    if profile.get("role") != "superAdmin":
        raise PermissionError("SuperAdmin required")
    return profile
