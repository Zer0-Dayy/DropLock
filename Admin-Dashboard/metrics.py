"""Metrics and locker state derivation helpers for the admin dashboard."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

LOCKER_STATES = ["AVAILABLE", "RESERVED", "OCCUPIED", "MAINTENANCE"]
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 120


@dataclass(slots=True)
class LockerView:
    """View model for locker data enriched with derived properties."""

    locker_id: str
    sector_id: str
    state: str
    active_booking_id: str | None
    tamper_flag: bool
    tamper_last_at: int | None
    last_heartbeat_at: int | None
    is_offline: bool
    raw: dict[str, Any]



def is_offline(last_heartbeat_at: int | None, heartbeat_timeout_sec: int, now_ms: int | None = None) -> bool:
    """Return whether the locker heartbeat is stale."""
    if not last_heartbeat_at:
        return False

    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    timeout_ms = max(1, heartbeat_timeout_sec) * 1000
    return (current_ms - int(last_heartbeat_at)) > timeout_ms



def build_locker_view(locker_id: str, sector_id: str, locker: dict[str, Any], heartbeat_timeout_sec: int) -> LockerView:
    """Build enriched locker information used by UI and alerts."""
    safe = locker or {}
    tamper_data = safe.get("tamper") or {}
    heartbeat = safe.get("lastHeartbeatAt")
    derived_offline = is_offline(heartbeat, heartbeat_timeout_sec)

    return LockerView(
        locker_id=locker_id,
        sector_id=sector_id,
        state=safe.get("state", "UNKNOWN"),
        active_booking_id=safe.get("activeBookingId"),
        tamper_flag=bool(tamper_data.get("flag")),
        tamper_last_at=tamper_data.get("lastAt"),
        last_heartbeat_at=heartbeat,
        is_offline=derived_offline,
        raw=safe,
    )



def compute_sector_metrics(locker_views: list[LockerView]) -> dict[str, int]:
    """Compute top-level sector locker metrics."""
    metrics = {
        "total": len(locker_views),
        "available": 0,
        "occupied": 0,
        "maintenance": 0,
        "offline": 0,
        "tampered": 0,
    }

    for locker in locker_views:
        if locker.state == "AVAILABLE":
            metrics["available"] += 1
        if locker.state == "OCCUPIED":
            metrics["occupied"] += 1
        if locker.state == "MAINTENANCE":
            metrics["maintenance"] += 1
        if locker.is_offline:
            metrics["offline"] += 1
        if locker.tamper_flag:
            metrics["tampered"] += 1

    return metrics
