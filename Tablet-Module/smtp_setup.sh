#!/bin/bash
# Copy this file to env.sh (or source it directly) and edit values.
# SMTP setup for DropLock token email notifications.

export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USERNAME="your-smtp-user@example.com"
export SMTP_PASSWORD="your-smtp-app-password"
export SMTP_USE_TLS="true"
export SMTP_FROM_EMAIL="droplock-notify@example.com"

# Optional runtime toggles
export UI_ENABLE_TK="true"
export UI_FULLSCREEN="true"

# Token expiration defaults
export COURIER_TOKEN_TTL_SEC="86400"
export PICKUP_TOKEN_TTL_SEC="259200"
