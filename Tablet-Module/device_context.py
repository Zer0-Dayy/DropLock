from session_models import AuthSession, DeviceContext
from firebase_repo import FirebaseRepo, FirebaseRepoError


class DeviceContextError(Exception):
    """Raised when the authenticated device is not allowed to run as a tablet."""


def load_device_context(auth_session: AuthSession, repo: FirebaseRepo) -> DeviceContext:
    profile = repo.get_profile(auth_session.device_uid)

    if profile is None:
        raise DeviceContextError(
            f"Device profile not found for uid={auth_session.device_uid}"
        )

    role = profile.get("role")
    status = profile.get("status")
    sector_id = profile.get("sectorId")
    display_name = profile.get("displayName") or auth_session.email
    profile_email = profile.get("email")

    if role != "device":
        raise DeviceContextError(
            f"Profile role must be 'device', got {role!r}"
        )

    if status != "active":
        raise DeviceContextError(
            f"Device status must be 'active', got {status!r}"
        )

    if not isinstance(sector_id, str) or not sector_id.strip():
        raise DeviceContextError("Device profile missing valid sectorId")

    if profile_email and profile_email != auth_session.email:
        raise DeviceContextError(
            "Authenticated email does not match profile email "
            f"({auth_session.email!r} != {profile_email!r})"
        )

    return DeviceContext(
        device_uid=auth_session.device_uid,
        email=auth_session.email,
        display_name=display_name,
        sector_id=sector_id.strip(),
        status=status,
        id_token=auth_session.id_token,
        refresh_token=auth_session.refresh_token,
        token_expires_in=auth_session.expires_in,
    )
