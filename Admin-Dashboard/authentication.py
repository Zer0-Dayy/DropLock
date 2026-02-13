"""Firebase Auth REST sign-in."""

from __future__ import annotations

import logging
import os

import requests

AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
LOGGER = logging.getLogger(__name__)



def sign_in(email: str, password: str) -> dict:
    api_key = os.getenv("FIREBASE_WEB_API_KEY")
    if not api_key:
        raise RuntimeError("Missing FIREBASE_WEB_API_KEY environment variable")

    payload = {"email": email, "password": password, "returnSecureToken": True}
    response = requests.post(f"{AUTH_URL}?key={api_key}", json=payload, timeout=15)
    if "application/json" not in response.headers.get("content-type", ""):
        raise RuntimeError("Non-JSON response received from Firebase sign-in")

    data = response.json()
    if response.status_code != 200:
        msg = data.get("error", {}).get("message", f"HTTP {response.status_code}")
        LOGGER.warning("Firebase sign-in failed for %s", email)
        raise RuntimeError(f"Firebase sign-in failed: {msg}")

    return data
