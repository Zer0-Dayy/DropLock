from __future__ import annotations

import json
import logging
from typing import Optional

from session_models import ControllerEvent, ControllerEventType
from mqtt_client import MQTTIncomingMessage


logger = logging.getLogger(__name__)


class ControllerEventParser:

    def parse(self, raw_msg: MQTTIncomingMessage) -> Optional[ControllerEvent]:
        payload_str = raw_msg.payload
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON payload topic=%s payload=%s",
                raw_msg.topic,
                payload_str,
            )
            return None

        if not isinstance(data, dict):
            logger.warning(
                "Payload is not a JSON object topic=%s payload=%s",
                raw_msg.topic,
                payload_str,
            )
            return None

        event_type_str = data.get("type")
        locker_id = data.get("lockerId")

        if not event_type_str or not isinstance(event_type_str, str):
            logger.warning(
                "Missing or invalid 'type' field topic=%s payload=%s",
                raw_msg.topic,
                payload_str,
            )
            return None

        if not locker_id or not isinstance(locker_id, str):
            logger.warning(
                "Missing or invalid 'lockerId' field topic=%s payload=%s",
                raw_msg.topic,
                payload_str,
            )
            return None

        try:
            event_type = ControllerEventType(event_type_str)
        except ValueError:
            logger.warning(
                "Unknown controller event type=%s topic=%s",
                event_type_str,
                raw_msg.topic,
            )
            return None

        request_id = data.get("requestId")
        if not request_id or not isinstance(request_id, str):
            request_id = ""

        payload = {
            k: v
            for k, v in data.items()
            if k not in ("type", "lockerId", "requestId", "sectorId", "schemaVersion")
        }

        event = ControllerEvent(
            event_type=event_type,
            locker_id=locker_id,
            request_id=request_id,
            payload=payload,
            received_at=raw_msg.received_at,
        )

        logger.info(
            "Parsed controller event type=%s locker=%s request_id=%s payload=%s",
            event.event_type,
            event.locker_id,
            event.request_id,
            event.payload,
        )

        return event
