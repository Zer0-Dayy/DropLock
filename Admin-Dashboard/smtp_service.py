"""SMTP notification helper for DropLock admin dashboard."""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent / "email_config.json"


class SmtpService:
    """Send notification emails from JSON-based SMTP settings."""

    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any] | None:
        if not self.config_path.exists():
            LOGGER.warning("email_config.json not found; email notifications disabled")
            return None

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                cfg = json.load(handle)
        except Exception as exc:
            LOGGER.warning("Failed to load email config: %s", exc)
            return None

        required = ["smtp_host", "smtp_port", "username", "password", "from_email", "use_tls"]
        if not all(key in cfg for key in required):
            LOGGER.warning("email_config.json missing required keys; email notifications disabled")
            return None
        return cfg

    @property
    def enabled(self) -> bool:
        return self._config is not None

    def send_alert_email(self, *, to_email: str, subject: str, body: str) -> bool:
        """Send a plain text alert email. Returns True when sent."""
        if not self._config:
            LOGGER.warning("Email send skipped because SMTP service is disabled")
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._config["from_email"]
        message["To"] = to_email
        message.set_content(body)

        try:
            with smtplib.SMTP(self._config["smtp_host"], int(self._config["smtp_port"]), timeout=15) as smtp:
                if bool(self._config.get("use_tls", True)):
                    smtp.starttls()
                smtp.login(self._config["username"], self._config["password"])
                smtp.send_message(message)
                LOGGER.info("Alert email sent to %s", to_email)
                return True
        except Exception as exc:
            LOGGER.warning("Failed to send alert email: %s", exc)
            return False
