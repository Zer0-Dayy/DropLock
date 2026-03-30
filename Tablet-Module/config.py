import os
from pathlib import Path


def require_env(name: str) -> str:
	value = os.getenv(name)
	if not value:
		raise RuntimeError(f"Missing required environment variable: {name}")
	return value

def optional_env(name: str, default):
	return os.getenv(name, default)





#_FIREBASE_CONFIG_#
FIREBASE_API_KEY = require_env("FIREBASE_API_KEY")
FIREBASE_DB_URL = require_env("FIREBASE_DB_URL")
FIREBASE_STORAGE_BUCKET = optional_env("FIREBASE_STORAGE_BUCKET", None)

#_DEVICE_CONFIG_#

DEVICE_EMAIL = require_env("DEVICE_EMAIL")
DEVICE_PASSWORD = require_env("DEVICE_PASSWORD")

#_MQTT_CONFIG_#

MQTT_HOST = require_env("MQTT_HOST")
MQTT_PORT = int(optional_env("MQTT_PORT", "8883"))
MQTT_CLIENT_ID = optional_env("MQTT_CLIENT_ID", "droplock-tablet")
MQTT_USERNAME = optional_env("MQTT_USERNAME", "") or None
MQTT_PASSWORD = optional_env("MQTT_PASSWORD", "") or None
MQTT_KEEPALIVE = int(optional_env("MQTT_KEEPALIVE", "60"))
MQTT_TLS_ENABLED = optional_env("MQTT_TLS_ENABLED", "false").lower() == "true"
MQTT_TLS_CA_CERT_PATH = optional_env("MQTT_TLS_CA_CERT_PATH", "")
MQTT_TLS_CLIENT_CERT_PATH = optional_env("MQTT_TLS_CLIENT_CERT_PATH", "")
MQTT_TLS_CLIENT_KEY_PATH = optional_env("MQTT_TLS_CLIENT_KEY_PATH", "")
MQTT_TLS_INSECURE_SKIP_VERIFY = (
    optional_env("MQTT_TLS_INSECURE_SKIP_VERIFY", "false").lower() == "true"
)

#_MQTT_TOPICS_#

MQTT_TOPIC_CMD = "droplock/{sector}/{locker}/cmd"
MQTT_TOPIC_EVENTS = "droplock/{sector}/{locker}/events"


#_SCANNER_CONFIG_#

SCANNER_DEBOUNCE_MS = int(optional_env("SCANNER_DEBOUNCE_MS", "1500"))


#_TIMEOUTS_CONFIG_#

OPEN_ACK_TIMEOUT_SEC = int(optional_env("OPEN_ACK_TIMEOUT_SEC", "8"))
CLOSE_ACK_TIMEOUT_SEC = int(optional_env("CLOSE_ACK_TIMEOUT_SEC", "8"))
SIGNATURE_TIMEPOUT_SEC = int(optional_env("SIGNATURE_TIMEOUT_SEC", "120"))


#_SIGNATURE_STORAGE_#

SIGNATURE_BASE_PATH = Path(optional_env("SIGNATURE_BASE_PATH", "runtime_storage"))
SIGNATURE_BASE_PATH.mkdir(parents=True, exist_ok=True)


#_WEIGHT_VALIDATION_#

WEIGHT_TOLERANCE_GRAMS = int(optional_env("WEIGHT_TOLERANCE_GRAMS", "50"))
