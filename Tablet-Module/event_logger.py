from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class EventLogger:
    """Structured event logger for scanner/session/controller lifecycle."""

    def __init__(self, logger_name: str = "droplock.event") -> None:
        self._logger = logging.getLogger(logger_name)

    def log(self, name: str, **data: Any) -> None:
        payload = {
            "event": name,
            "ts": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        self._logger.info(json.dumps(payload, ensure_ascii=False, default=str))
