from __future__ import annotations

import logging

from access_validator import validate_qr_token
from auth_client import AuthClient
from close_gates import CloseGateConfig, CloseGates
from config import (
    CLOSE_ACK_TIMEOUT_SEC,
    COURIER_TOKEN_TTL_SEC,
    DEVICE_EMAIL,
    DEVICE_PASSWORD,
    FIREBASE_API_KEY,
    MQTT_CLIENT_ID,
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS_CA_CERT_PATH,
    MQTT_TLS_CLIENT_CERT_PATH,
    MQTT_TLS_CLIENT_KEY_PATH,
    MQTT_TLS_ENABLED,
    MQTT_TLS_INSECURE_SKIP_VERIFY,
    MQTT_USERNAME,
    OPEN_ACK_TIMEOUT_SEC,
    PICKUP_TOKEN_TTL_SEC,
    SIGNATURE_BASE_PATH,
    UI_FULLSCREEN,
    WEIGHT_TOLERANCE_GRAMS,
    SMTP_FROM_EMAIL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USERNAME,
)
from controller_events import ControllerEventParser
from device_context import load_device_context
from email_notifier import EmailNotifier
from event_logger import EventLogger
from firebase_repo import FirebaseRepo
from mqtt_client import MQTTClient
from scanner_input import ScannerInput
from session_orchestrator import SessionOrchestrator
from signature_capture import SignatureCapture
from storage_service import StorageService
from token_service import TokenService
from ui_controller import UIController


logger = logging.getLogger(__name__)


class AccessValidatorAdapter:
    def __init__(self, *, repo: FirebaseRepo, device_context) -> None:
        self._repo = repo
        self._device_context = device_context

    def validate(self, *, token_id: str):
        return validate_qr_token(token_id=token_id, device_context=self._device_context, repo=self._repo)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    setup_logging()

    scanner = None
    mqtt_client = None

    try:
        auth = AuthClient(api_key=FIREBASE_API_KEY, email=DEVICE_EMAIL, password=DEVICE_PASSWORD)
        auth_session = auth.sign_in()
        repo = FirebaseRepo(id_token=auth_session.id_token)
        device_ctx = load_device_context(auth_session, repo)

        scanner = ScannerInput()
        mqtt_client = MQTTClient(
            sector_id=device_ctx.sector_id,
            broker_host=MQTT_HOST,
            broker_port=MQTT_PORT,
            client_id=MQTT_CLIENT_ID,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
            keepalive=MQTT_KEEPALIVE,
            enable_tls=MQTT_TLS_ENABLED,
            tls_ca_cert_path=MQTT_TLS_CA_CERT_PATH or None,
            tls_client_cert_path=MQTT_TLS_CLIENT_CERT_PATH or None,
            tls_client_key_path=MQTT_TLS_CLIENT_KEY_PATH or None,
            tls_insecure_skip_verify=MQTT_TLS_INSECURE_SKIP_VERIFY,
        )

        storage = StorageService(base_dir=SIGNATURE_BASE_PATH)
        storage.ensure_base_dirs()
        signature_capture = SignatureCapture(storage_service=storage)
        close_gates = CloseGates(
            CloseGateConfig(
                require_signature=True,
                require_weight=True,
                require_door_closed=False,
                weight_tolerance_grams=WEIGHT_TOLERANCE_GRAMS,
            )
        )

        validator = AccessValidatorAdapter(repo=repo, device_context=device_ctx)
        parser = ControllerEventParser()
        event_logger = EventLogger()
        ui_controller = UIController(enable_tk=False, fullscreen=UI_FULLSCREEN)
        token_service = TokenService(
            repo,
            courier_ttl_sec=COURIER_TOKEN_TTL_SEC,
            pickup_ttl_sec=PICKUP_TOKEN_TTL_SEC,
        )
        email_notifier = EmailNotifier(
            smtp_host=SMTP_HOST,
            smtp_port=SMTP_PORT,
            smtp_username=SMTP_USERNAME,
            smtp_password=SMTP_PASSWORD,
            from_email=SMTP_FROM_EMAIL,
            use_tls=SMTP_USE_TLS,
        )

        orchestrator = SessionOrchestrator(
            device_context=device_ctx,
            scanner_input=scanner,
            access_validator=validator,
            mqtt_client=mqtt_client,
            controller_event_parser=parser,
            firebase_repo=repo,
            signature_capture=signature_capture,
            close_gates=close_gates,
            token_service=token_service,
            ui_controller=ui_controller,
            event_logger=event_logger,
            email_notifier=email_notifier,
            open_ack_timeout_s=float(OPEN_ACK_TIMEOUT_SEC),
            close_ack_timeout_s=float(CLOSE_ACK_TIMEOUT_SEC),
        )
        orchestrator.run_forever()
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception:
        logger.exception("Fatal runtime error")
        return 1
    finally:
        if mqtt_client is not None:
            mqtt_client.stop()
        if scanner is not None:
            scanner.stop()


if __name__ == "__main__":
    raise SystemExit(main())
