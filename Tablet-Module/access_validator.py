from __future__ import annotations

import time
from typing import Any

from firebase_repo import FirebaseRepo, FirebaseRepoError
from session_models import DeviceContext, ValidationResult


VALID_BOOKING_STATUS_BY_PURPOSE = {
    "COURIER_DROP": "DROP_PENDING",
    "USER_PICKUP": "PICKUP_PENDING",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def validate_qr_token(
    token_id: str,
    device_context: DeviceContext,
    repo: FirebaseRepo,
) -> ValidationResult:
    try:
        token_data = repo.get_qr_token(token_id)
    except FirebaseRepoError as exc:
        return ValidationResult(
            allowed=False,
            reason=f"QR_TOKEN_READ_ERROR: {exc}",
            token_data=None,
            booking_data=None,
            locker_data=None,
        )

    if token_data is None:
        return ValidationResult(
            allowed=False,
            reason="TOKEN_NOT_FOUND",
            token_data=None,
            booking_data=None,
            locker_data=None,
        )

    used_at = token_data.get("usedAt")
    if isinstance(used_at, str):
        used_at = used_at.strip()
    if used_at not in (None, ""):
        return ValidationResult(
            allowed=False,
            reason="TOKEN_ALREADY_USED",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    expires_at = token_data.get("expiresAt")
    if not isinstance(expires_at, int):
        return ValidationResult(
            allowed=False,
            reason="TOKEN_INVALID_EXPIRES_AT",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if expires_at <= now_ms():
        return ValidationResult(
            allowed=False,
            reason="TOKEN_EXPIRED",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    token_sector_id = token_data.get("sectorId")
    locker_id = token_data.get("lockerId")
    booking_id = token_data.get("bookingId")
    purpose = token_data.get("purpose")

    if token_sector_id != device_context.sector_id:
        return ValidationResult(
            allowed=False,
            reason="TOKEN_WRONG_SECTOR",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if purpose not in VALID_BOOKING_STATUS_BY_PURPOSE:
        return ValidationResult(
            allowed=False,
            reason="TOKEN_INVALID_PURPOSE",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if not isinstance(booking_id, str) or not booking_id.strip():
        return ValidationResult(
            allowed=False,
            reason="TOKEN_MISSING_BOOKING_ID",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if not isinstance(locker_id, str) or not locker_id.strip():
        return ValidationResult(
            allowed=False,
            reason="TOKEN_MISSING_LOCKER_ID",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    try:
        booking_data = repo.get_booking(booking_id)
    except FirebaseRepoError as exc:
        return ValidationResult(
            allowed=False,
            reason=f"BOOKING_READ_ERROR: {exc}",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if booking_data is None:
        return ValidationResult(
            allowed=False,
            reason="BOOKING_NOT_FOUND",
            token_data=token_data,
            booking_data=None,
            locker_data=None,
        )

    if booking_data.get("sectorId") != token_sector_id:
        return ValidationResult(
            allowed=False,
            reason="BOOKING_SECTOR_MISMATCH",
            token_data=token_data,
            booking_data=booking_data,
            locker_data=None,
        )

    if booking_data.get("lockerId") != locker_id:
        return ValidationResult(
            allowed=False,
            reason="BOOKING_LOCKER_MISMATCH",
            token_data=token_data,
            booking_data=booking_data,
            locker_data=None,
        )

    required_status = VALID_BOOKING_STATUS_BY_PURPOSE[purpose]
    if booking_data.get("status") != required_status:
        return ValidationResult(
            allowed=False,
            reason="INVALID_BOOKING_STATUS",
            token_data=token_data,
            booking_data=booking_data,
            locker_data=None,
        )

    try:
        locker_data = repo.get_locker(token_sector_id, locker_id)
    except FirebaseRepoError as exc:
        return ValidationResult(
            allowed=False,
            reason=f"LOCKER_READ_ERROR: {exc}",
            token_data=token_data,
            booking_data=booking_data,
            locker_data=None,
        )

    if locker_data is None:
        return ValidationResult(
            allowed=False,
            reason="LOCKER_NOT_FOUND",
            token_data=token_data,
            booking_data=booking_data,
            locker_data=None,
        )

    return ValidationResult(
        allowed=True,
        reason="OK",
        token_data=token_data,
        booking_data=booking_data,
        locker_data=locker_data,
    )
