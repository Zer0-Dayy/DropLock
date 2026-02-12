# app.py
import streamlit as st

from authentication import sign_in
from admin_init import verify_id_token, init_firebase
from firebase_admin import db

from admin_ops import get_profile, assert_super_admin
from locker_actions import (
    admin_set_state,
    admin_request_open,
    super_create_locker,
    super_delete_locker,
)
from user_provisioning import provision_admin

LOCKER_STATES = ["AVAILABLE", "RESERVED", "OCCUPIED", "MAINTENANCE"]

st.set_page_config(page_title="DropLock Admin", layout="wide")


# ----------------------------
# Firebase helpers
# ----------------------------
def load_all_sectors():
    init_firebase()
    return db.reference("sectors").get() or {}


def load_lockers(sector_id: str):
    init_firebase()
    return db.reference(f"lockers/{sector_id}").get() or {}


# ----------------------------
# Session
# ----------------------------
def ensure_session():
    st.session_state.setdefault("idToken", None)
    st.session_state.setdefault("uid", None)
    st.session_state.setdefault("profile", None)


# ----------------------------
# Login
# ----------------------------
def login_view():
    st.title("DropLock Admin Dashboard")

    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

    if not ok:
        return

    try:
        data = sign_in(email, password)
        id_token = data["idToken"]
        claims = verify_id_token(id_token)
        uid = claims["uid"]

        profile = get_profile(uid)
        if not profile:
            st.error("No profile found in RTDB.")
            return
        if profile.get("status") != "active":
            st.error("Profile disabled.")
            return

        st.session_state.idToken = id_token
        st.session_state.uid = uid
        st.session_state.profile = profile
        st.rerun()

    except Exception as e:
        st.error(str(e))


# ----------------------------
# SuperAdmin: Admin management
# ----------------------------
def manage_admins(uid):
    st.markdown("## Create Admin")

    sectors = load_all_sectors()
    if not sectors:
        st.warning("Create a sector first.")
        return

    with st.form("create_admin"):
        email = st.text_input("Admin Email")
        pwd = st.text_input("Temporary Password", type="password")
        name = st.text_input("Display Name")
        sector_id = st.selectbox("Assign Sector", sorted(sectors.keys()))
        submit = st.form_submit_button("Create Admin")

    if not submit:
        return

    try:
        assert_super_admin(uid)

        if not email or not pwd or not name:
            raise ValueError("All fields required.")

        new_uid = provision_admin(email.strip(), pwd.strip(), sector_id, name.strip())
        st.success(f"Admin created (uid={new_uid})")

    except Exception as e:
        st.error(str(e))


# ----------------------------
# SuperAdmin: Locker management
# ----------------------------
def manage_lockers(uid, sector_id):
    st.markdown("## Manage Lockers")

    col1, col2, col3 = st.columns([2, 1, 1])
    locker_id = col1.text_input("Locker ID")

    if col2.button("Create"):
        try:
            if not locker_id.strip():
                raise ValueError("Locker ID required.")
            super_create_locker(uid, sector_id, locker_id.strip())
            st.success("Locker created.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

    if col3.button("Delete"):
        try:
            if not locker_id.strip():
                raise ValueError("Locker ID required.")
            super_delete_locker(uid, sector_id, locker_id.strip())
            st.success("Locker deleted.")
            st.rerun()
        except Exception as e:
            st.error(str(e))


# ----------------------------
# Locker table
# ----------------------------
def locker_table(uid, sector_id):
    lockers = load_lockers(sector_id)

    if not lockers:
        st.info("No lockers in this sector.")
        return

    st.markdown("## Lockers")

    for locker_id, data in sorted(lockers.items()):
        data = data or {}
        state = data.get("state", "UNKNOWN")
        booking = data.get("activeBookingId")

        cols = st.columns([1.2, 1, 1, 1.5, 2])

        cols[0].markdown(f"**{locker_id}**")
        cols[1].write(state)
        cols[2].write("BOOKED" if booking else "—")

        new_state = cols[3].selectbox(
            "state",
            LOCKER_STATES,
            index=LOCKER_STATES.index(state) if state in LOCKER_STATES else 0,
            key=f"state_{sector_id}_{locker_id}",
            label_visibility="collapsed",
        )

        action_cols = cols[4].columns(2)

        if action_cols[0].button("Apply", key=f"apply_{locker_id}"):
            try:
                admin_set_state(uid, sector_id, locker_id, new_state)
                st.success(f"{locker_id} updated.")
            except Exception as e:
                st.error(str(e))

        if action_cols[1].button("OPEN", key=f"open_{locker_id}"):
            try:
                cmd_id = admin_request_open(uid, sector_id, locker_id)
                st.success(f"OPEN requested ({cmd_id})")
            except Exception as e:
                st.error(str(e))


# ----------------------------
# Dashboard
# ----------------------------
def dashboard():
    uid = st.session_state.uid
    profile = st.session_state.profile
    role = profile.get("role")

    st.sidebar.write(f"UID: {uid}")
    st.sidebar.write(f"Role: {role}")
    st.sidebar.write(f"Sector: {profile.get('sectorId')}")

    if role == "superAdmin":
        sectors = load_all_sectors()
        if not sectors:
            st.info("No sectors defined.")
            return

        sector_id = st.selectbox("Select Sector", sorted(sectors.keys()))

        manage_admins(uid)
        manage_lockers(uid, sector_id)
        locker_table(uid, sector_id)
        return

    if role == "admin":
        sector_id = profile.get("sectorId")
        locker_table(uid, sector_id)
        return

    st.error("Access denied.")


# ----------------------------
# Main
# ----------------------------
def main():
    ensure_session()

    if not st.session_state.idToken:
        login_view()
    else:
        dashboard()


main()
