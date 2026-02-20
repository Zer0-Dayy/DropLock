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


def inject_global_styles() -> None:
    st.markdown(
        """
        <style>
            .block-container {padding-top: 1.2rem;}
            .droplock-hero {
                border-radius: 14px;
                padding: 1.2rem 1.4rem;
                margin-bottom: 0.8rem;
                background: linear-gradient(120deg, #1d3557, #457b9d 60%, #a8dadc);
                color: white;
                box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
            }
            .droplock-hero h2 {margin: 0;}
            .droplock-hero p {margin: .35rem 0 0 0; opacity: .95;}
            .droplock-panel {
                border: 1px solid rgba(120, 120, 120, 0.25);
                border-radius: 12px;
                padding: 0.8rem;
                margin-bottom: 0.65rem;
                background-color: rgba(250, 250, 250, 0.03);
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="droplock-hero">
            <h2>{title}</h2>
            <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
