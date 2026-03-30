from __future__ import annotations

import logging
from typing import Any

from auth_client import AuthClient
from close_gates import CloseGateConfig, CloseGates
from controller_events import ControllerEventParser
from firebase_repo import FirebaseRepo
from mqtt_client import MQTTClient
from scanner_input import ScannerInput
from session_orchestrator import SessionOrchestrator
from signature_capture import SignatureCapture
from storage_service import StorageService

from config import (
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
)

try:
    from config import SIGNATURE_BASE_PATH
except ImportError:
    SIGNATURE_BASE_PATH = "runtime_storage"

import device_context as device_context_module
import access_validator as access_validator_module


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_auth_client() -> AuthClient:
    return AuthClient(
        api_key=FIREBASE_API_KEY,
        email=DEVICE_EMAIL,
        password=DEVICE_PASSWORD,
    )


def sign_in(auth_client: AuthClient):
    if hasattr(auth_client, "sign_in"):
        return auth_client.sign_in()
    raise RuntimeError("AuthClient does not expose sign_in()")


def build_device_context(auth_session, firebase_repo):
    """
    Flexible adapter for your current device_context.py interface.
    """
    if hasattr(device_context_module, "build_device_context"):
        fn = device_context_module.build_device_context

        for call in (
            lambda: fn(auth_session=auth_session, firebase_repo=firebase_repo),
            lambda: fn(auth_session, firebase_repo),
            lambda: fn(auth_session=auth_session, repo=firebase_repo),
            lambda: fn(uid=auth_session.device_uid, firebase_repo=firebase_repo),
            lambda: fn(auth_session.device_uid, firebase_repo),
            lambda: fn(uid=auth_session.device_uid, repo=firebase_repo),
        ):
            try:
                return call()
            except TypeError:
                continue

    if hasattr(device_context_module, "load_device_context"):
        fn = device_context_module.load_device_context

        for call in (
            lambda: fn(auth_session=auth_session, firebase_repo=firebase_repo),
            lambda: fn(auth_session, firebase_repo),
            lambda: fn(auth_session=auth_session, repo=firebase_repo),
            lambda: fn(uid=auth_session.device_uid, firebase_repo=firebase_repo),
            lambda: fn(auth_session.device_uid, firebase_repo),
            lambda: fn(uid=auth_session.device_uid, repo=firebase_repo),
        ):
            try:
                return call()
            except TypeError:
                continue

    if hasattr(device_context_module, "DeviceContextLoader"):
        try:
            loader = device_context_module.DeviceContextLoader(firebase_repo=firebase_repo)
        except TypeError:
            try:
                loader = device_context_module.DeviceContextLoader(repo=firebase_repo)
            except TypeError as exc:
                raise RuntimeError(
                    "Could not construct DeviceContextLoader with known signatures"
                ) from exc

        if hasattr(loader, "build"):
            for call in (
                lambda: loader.build(auth_session=auth_session),
                lambda: loader.build(auth_session),
                lambda: loader.build(uid=auth_session.device_uid),
                lambda: loader.build(auth_session.device_uid),
            ):
                try:
                    return call()
                except TypeError:
                    continue

        if hasattr(loader, "load"):
            for call in (
                lambda: loader.load(auth_session=auth_session),
                lambda: loader.load(auth_session),
                lambda: loader.load(uid=auth_session.device_uid),
                lambda: loader.load(auth_session.device_uid),
            ):
                try:
                    return call()
                except TypeError:
                    continue

    raise RuntimeError(
        "Could not find a compatible device_context builder signature"
    )


class ModuleAccessValidatorAdapter:
    """
    Adapter for your function-based access_validator.py.
    """

    def __init__(self, module, firebase_repo, device_context):
        self._module = module
        self._firebase_repo = firebase_repo
        self._device_context = device_context

    def validate(self, token_id, sector_id=None):
        # First: exact match for your current file
        fn = getattr(self._module, "validate_qr_token", None)
        if fn is not None:
            return fn(
                token_id=token_id,
                device_context=self._device_context,
                repo=self._firebase_repo,
            )

        # Fallbacks if you rename later
        for fn_name in ("validate", "validate_token"):
            fn = getattr(self._module, fn_name, None)
            if fn is None:
                continue

            for call in (
                lambda: fn(
                    token_id=token_id,
                    device_context=self._device_context,
                    repo=self._firebase_repo,
                ),
                lambda: fn(
                    token_id=token_id,
                    device_context=self._device_context,
                    firebase_repo=self._firebase_repo,
                ),
                lambda: fn(token_id, self._device_context, self._firebase_repo),
                lambda: fn(token_id=token_id, sector_id=self._device_context.sector_id),
                lambda: fn(token_id, self._device_context.sector_id),
                lambda: fn(token_id),
            ):
                try:
                    return call()
                except TypeError:
                    continue

        raise RuntimeError(
            "Could not call access_validator module with known function signatures"
        )


def build_access_validator(firebase_repo, device_ctx):
    """
    Flexible adapter for your current access_validator.py interface.
    Supports:
    - class-based validators
    - module-level function validators
    """
    if hasattr(access_validator_module, "AccessValidator"):
        cls = access_validator_module.AccessValidator

        for ctor in (
            lambda: cls(firebase_repo=firebase_repo, device_context=device_ctx),
            lambda: cls(repo=firebase_repo, device_context=device_ctx),
            lambda: cls(firebase_repo=firebase_repo, sector_id=device_ctx.sector_id),
            lambda: cls(repo=firebase_repo, sector_id=device_ctx.sector_id),
            lambda: cls(device_context=device_ctx, firebase_repo=firebase_repo),
            lambda: cls(device_context=device_ctx, repo=firebase_repo),
            lambda: cls(firebase_repo, device_ctx),
            lambda: cls(firebase_repo),
            lambda: cls(),
        ):
            try:
                return ctor()
            except TypeError:
                continue

    if (
        hasattr(access_validator_module, "validate_qr_token")
        or hasattr(access_validator_module, "validate")
        or hasattr(access_validator_module, "validate_token")
    ):
        return ModuleAccessValidatorAdapter(
            module=access_validator_module,
            firebase_repo=firebase_repo,
            device_context=device_ctx,
        )

    raise RuntimeError(
        "Could not construct or adapt access_validator with known interfaces"
    )


def build_scanner() -> ScannerInput:
    scanner = ScannerInput()
    scanner.start()
    return scanner


def build_mqtt_client(device_ctx) -> MQTTClient:
    return MQTTClient(
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


def build_storage_service() -> StorageService:
    storage = StorageService(base_dir=SIGNATURE_BASE_PATH)
    storage.ensure_base_dirs()
    return storage


def build_signature_capture(storage_service: StorageService) -> SignatureCapture:
    return SignatureCapture(storage_service=storage_service)


def build_close_gates() -> CloseGates:
    return CloseGates(
        CloseGateConfig(
            require_signature=True,
            require_weight=True,
            require_door_closed=False,
            weight_tolerance_grams=100,
        )
    )


def build_controller_event_parser() -> ControllerEventParser:
    return ControllerEventParser()


def main() -> int:
    setup_logging()
    logger = logging.getLogger("main")

    logger.info("Starting DropLock tablet runtime")

    scanner: Any | None = None
    mqtt_client: Any | None = None

    try:
        auth_client = build_auth_client()
        auth_session = sign_in(auth_client)

        logger.info(
            "Authenticated device_uid=%s email=%s",
            getattr(auth_session, "device_uid", "<unknown>"),
            getattr(auth_session, "email", "<unknown>"),
        )

        firebase_repo = FirebaseRepo(id_token=auth_session.id_token)

        device_ctx = build_device_context(
            auth_session=auth_session,
            firebase_repo=firebase_repo,
        )

        logger.info(
            "Device context loaded device_uid=%s sector_id=%s display_name=%s status=%s",
            getattr(device_ctx, "device_uid", "<unknown>"),
            getattr(device_ctx, "sector_id", "<unknown>"),
            getattr(device_ctx, "display_name", "<unknown>"),
            getattr(device_ctx, "status", "<unknown>"),
        )

        scanner = build_scanner()
        logger.info("Scanner input started")

        access_validator = build_access_validator(
            firebase_repo=firebase_repo,
            device_ctx=device_ctx,
        )
        logger.info("Access validator initialized")

        mqtt_client = build_mqtt_client(device_ctx)
        logger.info("MQTT client initialized")

        controller_event_parser = build_controller_event_parser()

        storage_service = build_storage_service()
        logger.info("Storage service ready base_dir=%s", storage_service.base_dir)

        signature_capture = build_signature_capture(storage_service)
        close_gates = build_close_gates()

        orchestrator = SessionOrchestrator(
            device_context=device_ctx,
            scanner_input=scanner,
            access_validator=access_validator,
            mqtt_client=mqtt_client,
            controller_event_parser=controller_event_parser,
            firebase_repo=firebase_repo,
            signature_capture=signature_capture,
            close_gates=close_gates,
            ui_controller=None,
            event_logger=None,
            email_notifier=None,
        )

        logger.info("Session orchestrator ready")
        logger.info("Entering main runtime loop")

        orchestrator.run_forever()
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    except Exception:
        logger.exception("Fatal startup/runtime error")
        return 1

    finally:
        if mqtt_client is not None:
            try:
                mqtt_client.stop()
                logger.info("MQTT client stopped")
            except Exception:
                logger.exception("Failed to stop MQTT client cleanly")

        if scanner is not None:
            try:
                scanner.stop()
                logger.info("Scanner stopped")
            except Exception:
                logger.exception("Failed to stop scanner cleanly")


if __name__ == "__main__":
    raise SystemExit(main())
