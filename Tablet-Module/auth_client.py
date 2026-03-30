from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Dict

import requests

import config
from session_models import AuthSession


class AuthError(RuntimeError):
    """Raised when Firebase authentication fails."""


class AuthClient:
    def __init__(self, api_key: str, email: str, password: str, timeout_sec: int = 10):
        self.api_key = api_key
        self.email = email
        self.password = password
        self.timeout_sec = timeout_sec

    def sign_in(self) -> AuthSession:
        url = (
            "https://identitytoolkit.googleapis.com/v1/"
            f"accounts:signInWithPassword?key={self.api_key}"
        )

        payload = {
            "email": self.email,
            "password": self.password,
            "returnSecureToken": True,
        }

        data = self._post_json(url, payload)

        return AuthSession(
            device_uid=data["localId"],
            email=data["email"],
            id_token=data["idToken"],
            refresh_token=data["refreshToken"],
            expires_in=int(data["expiresIn"]),
            issued_at=datetime.utcnow(),
        )

    def refresh_id_token(self, auth_session: AuthSession) -> AuthSession:
        url = f"https://securetoken.googleapis.com/v1/token?key={self.api_key}"

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": auth_session.refresh_token,
        }

        data = self._post_form(url, payload)

        return AuthSession(
            device_uid=data["user_id"],
            email=auth_session.email,
            id_token=data["id_token"],
            refresh_token=data["refresh_token"],
            expires_in=int(data["expires_in"]),
            issued_at=datetime.utcnow(),
        )

    def ensure_valid_session(self, auth_session: AuthSession) -> AuthSession:
        if auth_session.is_expired():
            return self.refresh_id_token(auth_session)
        return auth_session

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            raise AuthError(f"Network error during Firebase sign-in: {exc}") from exc

        return self._parse_response(response)

    def _post_form(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(url, data=payload, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            raise AuthError(f"Network error during token refresh: {exc}") from exc

        return self._parse_response(response)

    def _parse_response(self, response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise AuthError(
                f"Firebase returned a non-JSON response: HTTP {response.status_code}"
            ) from exc

        if response.ok:
            return data

        error_code = (
            data.get("error", {})
            .get("message", "UNKNOWN_AUTH_ERROR")
        )

        raise AuthError(self._friendly_error_message(error_code))

    @staticmethod
    def _friendly_error_message(error_code: str) -> str:
        mapping = {
            "EMAIL_NOT_FOUND": "Device account email was not found.",
            "INVALID_PASSWORD": "Device account password is incorrect.",
            "USER_DISABLED": "Device account is disabled.",
            "INVALID_REFRESH_TOKEN": "Refresh token is invalid.",
            "TOKEN_EXPIRED": "Firebase token has expired.",
        }
        return mapping.get(error_code, f"Firebase authentication failed: {error_code}")


def build_auth_client() -> AuthClient:
    return AuthClient(
        api_key=config.FIREBASE_API_KEY,
        email=config.DEVICE_EMAIL,
        password=config.DEVICE_PASSWORD,
    )
