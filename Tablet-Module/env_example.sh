export FIREBASE_DB_URL="https://your-project-default-rtdb.firebaseio.com"
export FIREBASE_API_KEY="YOUR_FIREBASE_WEB_API_KEY"

export DEVICE_EMAIL="tablet-sector1@example.com"
export DEVICE_PASSWORD="DEVICE_ACCOUNT_PASSWORD"

export MQTT_HOST="127.0.0.1"
export MQTT_PORT="8883"
export MQTT_CLIENT_ID="droplock-tablet-sector1"
export MQTT_USERNAME=""
export MQTT_PASSWORD=""

export MQTT_TLS_ENABLED="false"
export MQTT_TLS_CA_CERT_PATH=""
export MQTT_TLS_CLIENT_CERT_PATH=""
export MQTT_TLS_CLIENT_KEY_PATH=""
export MQTT_TLS_INSECURE_SKIP_VERIFY="false"
# MQTT startup retries: 0 means retry forever; set a positive number to fail fast.
export MQTT_START_RETRY_DELAY_SEC="2.0"
export MQTT_START_MAX_ATTEMPTS="0"

export SIGNATURE_BASE_PATH="/home/pi/droplock/signatures"

# SMTP / email notification
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USERNAME="your-user@gmail.com"
export SMTP_PASSWORD="your-app-password"
export SMTP_USE_TLS="true"
export SMTP_FROM_EMAIL="droplock-notify@example.com"

# Token TTLs (seconds)
export COURIER_TOKEN_TTL_SEC="86400"
export PICKUP_TOKEN_TTL_SEC="259200"

# UI
export UI_ENABLE_TK="true"
export UI_FULLSCREEN="true"
