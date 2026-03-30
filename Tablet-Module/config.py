import os
from pathlib import Path


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def optional_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    return value


# Firebase
FIREBASE_API_KEY = require_env("FIREBASE_API_KEY")
FIREBASE_DB_URL = require_env("FIREBASE_DB_URL")

# Device auth
DEVICE_EMAIL = require_env("DEVICE_EMAIL")
DEVICE_PASSWORD = require_env("DEVICE_PASSWORD")

# MQTT
MQTT_HOST = require_env("MQTT_HOST")
MQTT_PORT = int(optional_env("MQTT_PORT", "8883") or "8883")
MQTT_CLIENT_ID = optional_env("MQTT_CLIENT_ID", "droplock-tablet") or "droplock-tablet"
MQTT_USERNAME = optional_env("MQTT_USERNAME", "") or None
MQTT_PASSWORD = optional_env("MQTT_PASSWORD", "") or None
MQTT_KEEPALIVE = int(optional_env("MQTT_KEEPALIVE", "60") or "60")
MQTT_TLS_ENABLED = (optional_env("MQTT_TLS_ENABLED", "false") or "false").lower() == "true"
MQTT_TLS_CA_CERT_PATH = optional_env("MQTT_TLS_CA_CERT_PATH", "") or ""
MQTT_TLS_CLIENT_CERT_PATH = optional_env("MQTT_TLS_CLIENT_CERT_PATH", "") or ""
MQTT_TLS_CLIENT_KEY_PATH = optional_env("MQTT_TLS_CLIENT_KEY_PATH", "") or ""
MQTT_TLS_INSECURE_SKIP_VERIFY = (
    (optional_env("MQTT_TLS_INSECURE_SKIP_VERIFY", "false") or "false").lower() == "true"
)

# Topic contracts
MQTT_TOPIC_CMD = "droplock/{sector}/{locker}/cmd"
MQTT_TOPIC_EVENTS = "droplock/{sector}/{locker}/events"

# Scanner
SCANNER_COOLDOWN_SECONDS = float(optional_env("SCANNER_COOLDOWN_SECONDS", "2.0") or "2.0")

# Runtime timeouts
OPEN_ACK_TIMEOUT_SEC = int(optional_env("OPEN_ACK_TIMEOUT_SEC", "20") or "20")
CLOSE_ACK_TIMEOUT_SEC = int(optional_env("CLOSE_ACK_TIMEOUT_SEC", "20") or "20")
SIGNATURE_TIMEOUT_SEC = int(optional_env("SIGNATURE_TIMEOUT_SEC", "120") or "120")

# Signature storage
SIGNATURE_BASE_PATH = Path(optional_env("SIGNATURE_BASE_PATH", "runtime_storage") or "runtime_storage")
SIGNATURE_BASE_PATH.mkdir(parents=True, exist_ok=True)

# Weight validation
WEIGHT_TOLERANCE_GRAMS = int(optional_env("WEIGHT_TOLERANCE_GRAMS", "50") or "50")

# Token issuance
COURIER_TOKEN_TTL_SEC = int(optional_env("COURIER_TOKEN_TTL_SEC", str(24 * 3600)) or str(24 * 3600))
PICKUP_TOKEN_TTL_SEC = int(optional_env("PICKUP_TOKEN_TTL_SEC", str(72 * 3600)) or str(72 * 3600))

# Email / SMTP
SMTP_HOST = optional_env("SMTP_HOST", "") or ""
SMTP_PORT = int(optional_env("SMTP_PORT", "587") or "587")
SMTP_USERNAME = optional_env("SMTP_USERNAME", "") or ""
SMTP_PASSWORD = optional_env("SMTP_PASSWORD", "") or ""
SMTP_USE_TLS = (optional_env("SMTP_USE_TLS", "true") or "true").lower() == "true"
SMTP_FROM_EMAIL = optional_env("SMTP_FROM_EMAIL", "") or ""

# UI
UI_ENABLE_TK = (optional_env("UI_ENABLE_TK", "false") or "false").lower() == "true"
UI_FULLSCREEN = (optional_env("UI_FULLSCREEN", "false") or "false").lower() == "true"
