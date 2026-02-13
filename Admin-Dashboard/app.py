"""DropLock Streamlit admin dashboard."""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path
from typing import Any

import streamlit as st
from firebase_admin import db

from admin_init import init_firebase, verify_id_token
from admin_ops import assert_super_admin, get_profile
from alert_service import AlertService
from authentication import sign_in
from locker_actions import admin_request_open, admin_set_state, super_create_locker, super_delete_locker
from locker_repo import load_all_sectors, load_lockers, update_sector_config
from metrics import LOCKER_STATES, DEFAULT_HEARTBEAT_TIMEOUT_SEC, LockerView, build_locker_view, compute_sector_metrics
from ui_components import format_state, format_ts, render_metrics, tamper_badge
from user_provisioning import list_admin_profiles, provision_admin, reset_admin_password, set_admin_status

st.set_page_config(page_title="DropLock Admin", layout="wide")

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "admin_dashboard.log"), logging.StreamHandler()],
)
LOGGER = logging.getLogger(__name__)
ALERTS = AlertService()



def ensure_session() -> None:
    st.session_state.setdefault("idToken", None)
    st.session_state.setdefault("uid", None)
    st.session_state.setdefault("profile", None)
    st.session_state.setdefault("locker_signals", {})



def login_view() -> None:
    st.title("DropLock Admin Dashboard")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")
    if not ok:
        return

    try:
        data = sign_in(email, password)
        claims = verify_id_token(data["idToken"])
        uid = claims["uid"]
        profile = get_profile(uid)
        if not profile:
            st.error("No profile found in RTDB")
            return
        if profile.get("status") != "active":
            st.error("Profile disabled")
            return

        st.session_state.idToken = data["idToken"]
        st.session_state.uid = uid
        st.session_state.profile = profile
        st.rerun()
    except Exception as exc:
        st.error(str(exc))



def _selected_sector(profile: dict[str, Any], sectors: dict[str, Any]) -> str | None:
    role = profile.get("role")
    if role == "admin":
        return profile.get("sectorId")
    if not sectors:
        return None
    return st.sidebar.selectbox("Sector", sorted(sectors.keys()))



def _build_locker_views(sector_id: str, sectors: dict[str, Any]) -> list[LockerView]:
    lockers = load_lockers(sector_id)
    heartbeat_timeout = int((sectors.get(sector_id, {}).get("config") or {}).get("heartbeatTimeoutSec", DEFAULT_HEARTBEAT_TIMEOUT_SEC))
    return [build_locker_view(locker_id, sector_id, data or {}, heartbeat_timeout) for locker_id, data in sorted(lockers.items())]



def _trigger_derived_alerts(uid: str, locker_views: list[LockerView]) -> None:
    recipient = os.getenv("DROPLOCK_ALERT_RECIPIENT") or (get_profile(uid) or {}).get("email")
    for locker in locker_views:
        signal_key = f"{locker.sector_id}:{locker.locker_id}"
        prev = st.session_state.locker_signals.get(signal_key, {"tamper": False, "offline": False})

        if locker.tamper_flag and not prev.get("tamper"):
            alert_id = ALERTS.create_alert(
                alert_type="TAMPER",
                sector_id=locker.sector_id,
                locker_id=locker.locker_id,
                severity="HIGH",
                actor_uid=uid,
                booking_id=locker.active_booking_id,
            )
            if alert_id:
                ALERTS.maybe_send_email(
                    recipient=recipient,
                    alert_type="TAMPER",
                    sector_id=locker.sector_id,
                    locker_id=locker.locker_id,
                    actor_uid=uid,
                    booking_id=locker.active_booking_id,
                )

        if locker.is_offline and not prev.get("offline"):
            alert_id = ALERTS.create_alert(
                alert_type="OFFLINE",
                sector_id=locker.sector_id,
                locker_id=locker.locker_id,
                severity="MEDIUM",
                actor_uid=uid,
                booking_id=locker.active_booking_id,
            )
            if alert_id:
                ALERTS.maybe_send_email(
                    recipient=recipient,
                    alert_type="OFFLINE",
                    sector_id=locker.sector_id,
                    locker_id=locker.locker_id,
                    actor_uid=uid,
                    booking_id=locker.active_booking_id,
                )

        st.session_state.locker_signals[signal_key] = {"tamper": locker.tamper_flag, "offline": locker.is_offline}



def dashboard_page(locker_views: list[LockerView]) -> None:
    st.subheader("Dashboard")
    metrics = compute_sector_metrics(locker_views)
    render_metrics(metrics)
    total = metrics.get("total", 0)
    online_pct = 0 if total == 0 else round(((total - metrics.get("offline", 0)) / total) * 100, 1)
    st.info(f"System status: {online_pct}% online | {metrics.get('tampered', 0)} tampered lockers")



def sectors_page(uid: str, role: str, sectors: dict[str, Any], sector_id: str | None) -> None:
    st.subheader("Sectors")
    if not sector_id:
        st.info("No sectors found")
        return

    sector = sectors.get(sector_id, {})
    config = sector.get("config") or {}
    st.write("Current config", config)

    if role != "superAdmin":
        return

    with st.form("sector_config"):
        heartbeat = st.number_input("heartbeatTimeoutSec", min_value=10, value=int(config.get("heartbeatTimeoutSec", DEFAULT_HEARTBEAT_TIMEOUT_SEC)))
        pulse = st.number_input("openPulseMs", min_value=50, value=int(config.get("openPulseMs", 500)))
        timezone = st.text_input("timezone", value=str(config.get("timezone", "UTC")))
        save = st.form_submit_button("Save config")

    if save:
        assert_super_admin(uid)
        update_sector_config(sector_id, {"heartbeatTimeoutSec": int(heartbeat), "openPulseMs": int(pulse), "timezone": timezone})
        st.success("Sector config updated")



def lockers_page(uid: str, role: str, sector_id: str | None, locker_views: list[LockerView]) -> None:
    st.subheader("Lockers")
    if not sector_id:
        st.info("No sector selected")
        return

    if role == "superAdmin":
        col1, col2, col3 = st.columns([2, 1, 1])
        locker_id = col1.text_input("Locker ID")
        if col2.button("Create"):
            super_create_locker(uid, sector_id, locker_id.strip())
            st.success("Locker created")
            st.rerun()
        if col3.button("Delete"):
            super_delete_locker(uid, sector_id, locker_id.strip())
            st.success("Locker deleted")
            st.rerun()

    filter_cols = st.columns(4)
    f_booked = filter_cols[0].checkbox("Booked only")
    f_maintenance = filter_cols[1].checkbox("Maintenance only")
    f_tampered = filter_cols[2].checkbox("Tampered only")
    f_offline = filter_cols[3].checkbox("Offline only")

    filtered = locker_views
    if f_booked:
        filtered = [l for l in filtered if l.active_booking_id]
    if f_maintenance:
        filtered = [l for l in filtered if l.state == "MAINTENANCE"]
    if f_tampered:
        filtered = [l for l in filtered if l.tamper_flag]
    if f_offline:
        filtered = [l for l in filtered if l.is_offline]

    if not filtered:
        st.info("No lockers match filters")
        return

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["lockerId", "state", "bookingId", "tamper", "offline", "lastHeartbeatAt"])

    for locker in filtered:
        writer.writerow(
            [locker.locker_id, locker.state, locker.active_booking_id or "", locker.tamper_flag, locker.is_offline, locker.last_heartbeat_at or ""]
        )

        cols = st.columns([1.2, 1, 1, 1, 1.5, 2])
        cols[0].markdown(f"**{locker.locker_id}**")
        cols[1].write(format_state(locker))
        cols[2].write(locker.active_booking_id or "—")
        cols[3].write(tamper_badge(locker))
        cols[4].write(format_ts(locker.last_heartbeat_at))

        new_state = cols[5].selectbox(
            "state",
            LOCKER_STATES,
            index=LOCKER_STATES.index(locker.state) if locker.state in LOCKER_STATES else 0,
            key=f"state_{sector_id}_{locker.locker_id}",
            label_visibility="collapsed",
        )

        action_cols = st.columns([1, 1, 4])
        if action_cols[0].button("Apply", key=f"apply_{locker.locker_id}"):
            admin_set_state(uid, sector_id, locker.locker_id, new_state)
            st.success(f"{locker.locker_id} updated")
            st.rerun()
        if action_cols[1].button("OPEN", key=f"open_{locker.locker_id}"):
            cmd_id = admin_request_open(uid, sector_id, locker.locker_id)
            st.success(f"OPEN requested ({cmd_id})")

    st.download_button("Export CSV", csv_buffer.getvalue(), file_name=f"lockers_{sector_id}.csv", mime="text/csv")



def alerts_page(uid: str, role: str, sector_id: str | None) -> None:
    st.subheader("Alerts")
    alerts = ALERTS.list_alerts()
    if not alerts:
        st.info("No alerts")
        return

    for alert_id, alert in sorted(alerts.items(), key=lambda i: (i[1] or {}).get("createdAt", 0), reverse=True):
        safe = alert or {}
        if sector_id and role == "admin" and safe.get("sectorId") != sector_id:
            continue

        cols = st.columns([1, 1, 1, 1, 1.2, 2])
        cols[0].write(safe.get("type"))
        cols[1].write(f"{safe.get('sectorId')}/{safe.get('lockerId')}")
        cols[2].write(safe.get("severity"))
        cols[3].write(safe.get("status"))
        cols[4].write(format_ts(safe.get("createdAt")))

        actions = cols[5].columns(2)
        if role == "superAdmin" and safe.get("status") == "OPEN":
            if actions[0].button("ACK", key=f"ack_{alert_id}"):
                ALERTS.update_status(alert_id, "ACKED", uid)
                st.rerun()
        if role == "superAdmin" and safe.get("status") in {"OPEN", "ACKED"}:
            if actions[1].button("Close", key=f"close_{alert_id}"):
                ALERTS.update_status(alert_id, "CLOSED", uid)
                st.rerun()



def admin_mgmt_page(uid: str, role: str, sectors: dict[str, Any]) -> None:
    st.subheader("Admin Management")
    if role != "superAdmin":
        st.error("SuperAdmin only")
        return

    with st.form("create_admin"):
        email = st.text_input("Admin Email")
        temp_password = st.text_input("Temporary Password", type="password")
        name = st.text_input("Display Name")
        sector_id = st.selectbox("Assign Sector", sorted(sectors.keys()) if sectors else [])
        submit = st.form_submit_button("Create Admin")
    if submit:
        assert_super_admin(uid)
        new_uid = provision_admin(email.strip(), temp_password.strip(), sector_id, name.strip())
        st.success(f"Admin created ({new_uid})")

    admins = list_admin_profiles()
    for admin_uid, profile in admins.items():
        cols = st.columns([1.5, 1, 1, 2])
        cols[0].write(profile.get("email"))
        cols[1].write(profile.get("status", "active"))
        cols[2].write(profile.get("sectorId"))

        actions = cols[3].columns(3)
        if profile.get("status") == "active":
            if actions[0].button("Disable", key=f"disable_{admin_uid}"):
                set_admin_status(admin_uid, "disabled")
                st.rerun()
        else:
            if actions[0].button("Reactivate", key=f"reactivate_{admin_uid}"):
                set_admin_status(admin_uid, "active")
                st.rerun()

        if actions[1].button("Reset Password", key=f"reset_{admin_uid}"):
            temp = reset_admin_password(admin_uid)
            st.warning(f"Temporary password for {admin_uid}: {temp}")



def dashboard() -> None:
    init_firebase()
    uid = st.session_state.uid
    profile = st.session_state.profile
    role = profile.get("role")

    sectors = load_all_sectors()
    sector_id = _selected_sector(profile, sectors)

    st.sidebar.write(f"UID: {uid}")
    st.sidebar.write(f"Role: {role}")
    st.sidebar.write(f"Sector: {profile.get('sectorId')}")
    if st.sidebar.button("Logout"):
        for key in ("idToken", "uid", "profile"):
            st.session_state[key] = None
        st.rerun()

    pages = ["Dashboard", "Sectors", "Lockers", "Alerts"]
    if role == "superAdmin":
        pages.append("Admin Management")
    page = st.sidebar.radio("Navigation", pages)

    locker_views = _build_locker_views(sector_id, sectors) if sector_id else []
    _trigger_derived_alerts(uid, locker_views)

    if page == "Dashboard":
        dashboard_page(locker_views)
    elif page == "Sectors":
        sectors_page(uid, role, sectors, sector_id)
    elif page == "Lockers":
        lockers_page(uid, role, sector_id, locker_views)
    elif page == "Alerts":
        alerts_page(uid, role, sector_id)
    elif page == "Admin Management":
        admin_mgmt_page(uid, role, sectors)



def main() -> None:
    ensure_session()
    if not st.session_state.idToken:
        login_view()
    else:
        dashboard()


if __name__ == "__main__":
    main()
