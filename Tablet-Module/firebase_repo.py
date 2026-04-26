from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import requests
import time
import uuid

from config import FIREBASE_DB_URL


DEFAULT_TIMEOUT_SECONDS = 10


class FirebaseRepoError(Exception):
    """Raised when Firebase Realtime Database access fails."""


@dataclass(slots=True)
class FirebaseRepo:
    id_token: str
    db_url: str = FIREBASE_DB_URL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _normalize_db_url(self) -> str:
        return self.db_url.rstrip("/")

    def _build_url(self, path: str) -> str:
        safe_path = "/".join(
            quote(part, safe="") for part in path.strip("/").split("/")
        )
        return f"{self._normalize_db_url()}/{safe_path}.json"

    def _request(
        self,
        *,
        method: str,
        path: str,
        data: Any | None = None,
    ) -> Any:
        url = self._build_url(path)

        try:
            response = requests.request(
                method=method,
                url=url,
                params={"auth": self.id_token},
                json=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FirebaseRepoError(
                f"Network error while {method} '{path}': {exc}"
            ) from exc

        if response.status_code != 200:
            raise FirebaseRepoError(
                f"Firebase {method} failed for '{path}' "
                f"(status={response.status_code}, body={response.text})"
            )

        if not response.text:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise FirebaseRepoError(
                f"Invalid JSON returned by Firebase for {method} '{path}'"
            ) from exc

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _make_event_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Raw HTTP wrappers
    # ------------------------------------------------------------------

    def get_json(self, path: str) -> Optional[Any]:
        return self._request(method="GET", path=path)

    def put_json(self, path: str, data: Any) -> Any:
        return self._request(method="PUT", path=path, data=data)

    def post_json(self, path: str, data: Any) -> Any:
        return self._request(method="POST", path=path, data=data)

    def patch_json(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        result = self._request(method="PATCH", path=path, data=data)
        if result is not None and not isinstance(result, dict):
            raise FirebaseRepoError(
                f"Expected object from PATCH '{path}', got {type(result).__name__}"
            )
        return result or {}

    def delete_json(self, path: str) -> Any:
        return self._request(method="DELETE", path=path)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_profile(self, uid: str) -> Optional[dict[str, Any]]:
        data = self.get_json(f"profiles/{uid}")
        if data is not None and not isinstance(data, dict):
            raise FirebaseRepoError(
                f"Expected object at profiles/{uid}, got {type(data).__name__}"
            )
        return data

    def get_qr_token(self, token_id: str) -> Optional[dict[str, Any]]:
        data = self.get_json(f"qrTokens/{token_id}")
        if data is not None and not isinstance(data, dict):
            raise FirebaseRepoError(
                f"Expected object at qrTokens/{token_id}, got {type(data).__name__}"
            )
        return data

    def get_booking(self, booking_id: str) -> Optional[dict[str, Any]]:
        data = self.get_json(f"bookings/{booking_id}")
        if data is not None and not isinstance(data, dict):
            raise FirebaseRepoError(
                f"Expected object at bookings/{booking_id}, got {type(data).__name__}"
            )
        return data

    def get_locker(self, sector_id: str, locker_id: str) -> Optional[dict[str, Any]]:
        data = self.get_json(f"lockers/{sector_id}/{locker_id}")
        if data is not None and not isinstance(data, dict):
            raise FirebaseRepoError(
                f"Expected object at lockers/{sector_id}/{locker_id}, got {type(data).__name__}"
            )
        return data

    def get_admin_commands(self, sector_id: str) -> dict[str, dict[str, Any]]:
        data = self.get_json(f"adminCommands/{sector_id}")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise FirebaseRepoError(
                f"Expected object at adminCommands/{sector_id}, got {type(data).__name__}"
            )
        return data

    def delete_admin_command(self, *, sector_id: str, locker_id: str, cmd_id: str) -> None:
        self.delete_json(f"adminCommands/{sector_id}/{locker_id}/{cmd_id}")

    # ------------------------------------------------------------------
    # QR token helpers
    # ------------------------------------------------------------------

    def mark_qr_token_used(
        self,
        token_id: str,
        used_at_ms: int | None = None,
    ) -> dict[str, Any]:
        if used_at_ms is None:
            used_at_ms = self._now_ms()

        return self.patch_json(
            f"qrTokens/{token_id}",
            {"usedAt": used_at_ms},
        )

    # ------------------------------------------------------------------
    # Booking event helpers
    # ------------------------------------------------------------------

    def append_booking_event(
        self,
        *,
        booking_id: str,
        event_type: str,
        actor_uid: str,
        data: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> str:
        event_id = self._make_event_id("evt")
        payload = {
            "type": event_type,
            "ts": ts_ms if ts_ms is not None else self._now_ms(),
            "actorUid": actor_uid,
            "data": data or {},
        }

        self.put_json(
            f"bookingEvents/{booking_id}/{event_id}",
            payload,
        )
        return event_id

    # ------------------------------------------------------------------
    # Booking update helpers
    # ------------------------------------------------------------------

    def update_booking_measured_weight(
        self,
        *,
        booking_id: str,
        measured_weight_grams: int,
        updated_at_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.patch_json(
            f"bookings/{booking_id}",
            {
                "measuredWeightGrams": measured_weight_grams,
                "updatedAt": updated_at_ms if updated_at_ms is not None else self._now_ms(),
            },
        )

    def update_booking_status(
        self,
        *,
        booking_id: str,
        status: str,
        updated_at_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.patch_json(
            f"bookings/{booking_id}",
            {
                "status": status,
                "updatedAt": updated_at_ms if updated_at_ms is not None else self._now_ms(),
            },
        )

    def update_booking_status_post_session(
        self,
        *,
        booking_id: str,
        purpose: str,
    ) -> dict[str, Any]:
        """
        Purpose-aware booking transition after successful CLOSE_ACK.
        """
        if purpose == "COURIER_DROP":
            next_status = "OCCUPIED"
        elif purpose == "USER_PICKUP":
            next_status = "COMPLETED"
        else:
            raise FirebaseRepoError(
                f"Unknown purpose for post-session booking update: {purpose}"
            )

        return self.update_booking_status(
            booking_id=booking_id,
            status=next_status,
        )

    # ------------------------------------------------------------------
    # Locker update helpers
    # ------------------------------------------------------------------

    def update_locker_heartbeat(
        self,
        *,
        sector_id: str,
        locker_id: str,
        heartbeat_at_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.patch_json(
            f"lockers/{sector_id}/{locker_id}",
            {
                "lastHeartbeatAt": heartbeat_at_ms if heartbeat_at_ms is not None else self._now_ms(),
            },
        )

    def update_locker_tamper(
        self,
        *,
        sector_id: str,
        locker_id: str,
        tamper_flag: bool,
        tamper_at_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.patch_json(
            f"lockers/{sector_id}/{locker_id}/tamper",
            {
                "flag": tamper_flag,
                "lastAt": tamper_at_ms if tamper_at_ms is not None else self._now_ms(),
            },
        )

    def update_locker_state(
        self,
        *,
        sector_id: str,
        locker_id: str,
        state: str,
        active_booking_id: str | None,
        changed_at_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.patch_json(
            f"lockers/{sector_id}/{locker_id}",
            {
                "state": state,
                "activeBookingId": active_booking_id,
                "lastChangedAt": changed_at_ms if changed_at_ms is not None else self._now_ms(),
            },
        )

    def update_locker_state_post_session(
        self,
        *,
        sector_id: str,
        locker_id: str,
        booking_id: str,
        purpose: str,
    ) -> None:
        """
        Purpose-aware locker transition after successful CLOSE_ACK.
        """
        if purpose == "COURIER_DROP":
            state = "OCCUPIED"
            active_booking_id: str | None = booking_id
        elif purpose == "USER_PICKUP":
            state = "AVAILABLE"
            active_booking_id = None
        else:
            raise FirebaseRepoError(
                f"Unknown purpose for post-session locker update: {purpose}"
            )

        self.update_locker_state(
            sector_id=sector_id,
            locker_id=locker_id,
            state=state,
            active_booking_id=active_booking_id,
        )

        # Keep the activeBookingByLocker index aligned if present
        index_path = f"indexes/activeBookingByLocker/{sector_id}/{locker_id}"
        if active_booking_id is None:
            self.delete_json(index_path)
        else:
            self.put_json(index_path, active_booking_id)

    # ------------------------------------------------------------------
    # Unlock request / grant helpers
    # ------------------------------------------------------------------

    def create_unlock_request(
        self,
        *,
        token_id: str,
        booking_id: str,
        sector_id: str,
        locker_id: str,
        actor_uid: str,
        ts_ms: int | None = None,
        status: str = "PENDING",
        request_id: str | None = None,
    ) -> str:
        req_id = request_id or self._make_event_id("unlock")

        payload = {
            "tokenId": token_id,
            "bookingId": booking_id,
            "sectorId": sector_id,
            "lockerId": locker_id,
            "actorUid": actor_uid,
            "ts": ts_ms if ts_ms is not None else self._now_ms(),
            "status": status,
        }

        self.put_json(f"unlockRequests/{req_id}", payload)
        return req_id

    def create_unlock_grant(
        self,
        *,
        request_id: str,
        granted: bool,
        reason: str,
        mqtt_topic: str | None = None,
        mqtt_payload: str | None = None,
        ts_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "granted": granted,
            "reason": reason,
            "ts": ts_ms if ts_ms is not None else self._now_ms(),
        }

        if mqtt_topic is not None or mqtt_payload is not None:
            payload["mqtt"] = {
                "topic": mqtt_topic or "",
                "payload": mqtt_payload or "",
            }

        self.put_json(f"unlockGrants/{request_id}", payload)
