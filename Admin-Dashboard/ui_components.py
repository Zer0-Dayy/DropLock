"""Reusable Streamlit UI components."""

from __future__ import annotations

import datetime as dt
import streamlit as st

from metrics import LockerView

STATE_COLORS = {
    "AVAILABLE": "🟢 AVAILABLE",
    "OCCUPIED": "🔵 OCCUPIED",
    "RESERVED": "🟠 RESERVED",
    "MAINTENANCE": "🔴 MAINTENANCE",
}


def render_metrics(metrics: dict[str, int]) -> None:
    cols = st.columns(5)
    cols[0].metric("Total Lockers", metrics.get("total", 0))
    cols[1].metric("Available", metrics.get("available", 0))
    cols[2].metric("Occupied", metrics.get("occupied", 0))
    cols[3].metric("Maintenance", metrics.get("maintenance", 0))
    cols[4].metric("Offline", metrics.get("offline", 0))


def format_state(locker: LockerView) -> str:
    if locker.is_offline:
        return "⚫ OFFLINE"
    return STATE_COLORS.get(locker.state, locker.state)


def format_ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return "—"
    return dt.datetime.fromtimestamp(ts_ms / 1000.0).isoformat(timespec="seconds")


def tamper_badge(locker: LockerView) -> str:
    return "⚠️ Tamper" if locker.tamper_flag else "—"
