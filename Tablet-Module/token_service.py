from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

from firebase_repo import FirebaseRepo


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token_id: str
    booking_id: str
    purpose: str
    sector_id: str
    locker_id: str
    expires_at: int
    issued_to_uid: Optional[str]


class TokenService:
    PURPOSE_COURIER_DROP = "COURIER_DROP"
    PURPOSE_USER_PICKUP = "USER_PICKUP"

    def __init__(
        self,
        repo: FirebaseRepo,
        *,
        courier_ttl_sec: int,
        pickup_ttl_sec: int,
    ) -> None:
        self._repo = repo
        self._courier_ttl_sec = courier_ttl_sec
        self._pickup_ttl_sec = pickup_ttl_sec

    def issue_courier_drop_token(self, booking_id: str) -> IssuedToken:
        booking = self._require_booking(booking_id)
        if booking.get("status") != "BOOKED":
            raise ValueError("Courier token can only be issued for BOOKED booking")

        token = self._create_token(
            booking_id=booking_id,
            purpose=self.PURPOSE_COURIER_DROP,
            sector_id=booking["sectorId"],
            locker_id=booking["lockerId"],
            issued_to_uid=booking.get("courierId") or None,
            ttl_sec=self._courier_ttl_sec,
        )
        self._repo.update_booking_status(booking_id=booking_id, status="DROP_PENDING")
        return token

    def issue_user_pickup_token(self, booking_id: str) -> IssuedToken:
        booking = self._require_booking(booking_id)
        if booking.get("status") != "OCCUPIED":
            raise ValueError("Pickup token can only be issued for OCCUPIED booking")

        token = self._create_token(
            booking_id=booking_id,
            purpose=self.PURPOSE_USER_PICKUP,
            sector_id=booking["sectorId"],
            locker_id=booking["lockerId"],
            issued_to_uid=booking.get("userId") or None,
            ttl_sec=self._pickup_ttl_sec,
        )
        self._repo.update_booking_status(booking_id=booking_id, status="PICKUP_PENDING")
        return token

    def _require_booking(self, booking_id: str) -> dict:
        booking = self._repo.get_booking(booking_id)
        if not booking:
            raise ValueError(f"Booking not found: {booking_id}")
        for field in ("sectorId", "lockerId"):
            if not isinstance(booking.get(field), str) or not booking[field].strip():
                raise ValueError(f"Booking missing required field: {field}")
        return booking

    def _create_token(
        self,
        *,
        booking_id: str,
        purpose: str,
        sector_id: str,
        locker_id: str,
        issued_to_uid: Optional[str],
        ttl_sec: int,
    ) -> IssuedToken:
        token_id = self._new_token_id(purpose)
        expires_at = int(time.time() * 1000) + (ttl_sec * 1000)

        payload = {
            "bookingId": booking_id,
            "purpose": purpose,
            "sectorId": sector_id,
            "lockerId": locker_id,
            "expiresAt": expires_at,
            "usedAt": None,
            "issuedToUid": issued_to_uid or None,
        }
        self._repo.put_json(f"qrTokens/{token_id}", payload)
        return IssuedToken(
            token_id=token_id,
            booking_id=booking_id,
            purpose=purpose,
            sector_id=sector_id,
            locker_id=locker_id,
            expires_at=expires_at,
            issued_to_uid=issued_to_uid,
        )

    @staticmethod
    def _new_token_id(purpose: str) -> str:
        prefix = "cdr" if purpose == "COURIER_DROP" else "upk"
        return f"{prefix}_{uuid.uuid4().hex}"
