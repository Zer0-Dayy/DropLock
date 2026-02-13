"""Firebase initialization and token verification."""

from __future__ import annotations

import os

import firebase_admin
from firebase_admin import auth, credentials



def init_firebase() -> None:
    if firebase_admin._apps:
        return

    sa_path = os.getenv("FIREBASE_SA_PATH")
    db_url = os.getenv("FIREBASE_DB_URL")
    if not sa_path or not db_url:
        raise RuntimeError("Missing FIREBASE_SA_PATH or FIREBASE_DB_URL")

    cred = credentials.Certificate(sa_path)
    firebase_admin.initialize_app(cred, {"databaseURL": db_url})



def verify_id_token(id_token: str) -> dict:
    init_firebase()
    return auth.verify_id_token(id_token)
