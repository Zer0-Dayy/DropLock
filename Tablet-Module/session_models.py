from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime, timedelta


class SessionPhase(Enum):
    IDLE = "IDLE"
    VALIDATING_QR = "VALIDATING_QR"
    UNLOCKING = "UNLOCKING"
    WAITING_FOR_SIGNATURE = "WAITING_FOR_SIGNATURE"
    WAITING_FOR_OTHER_GATES = "WAITING_FOR_OTHER_GATES"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    CLOSING = "CLOSING"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    ERROR = "ERROR"


@dataclass
class AuthSession:
    device_uid: str
    email: str
    id_token: str
    refresh_token: str
    expires_in: int
    issued_at: datetime

    def expires_at(self) -> datetime:
        return self.issued_at + timedelta(seconds=self.expires_in)

    def is_expired(self, skew_seconds: int = 30) -> bool:
        return datetime.utcnow() >= (self.expires_at() - timedelta(seconds=skew_seconds))


@dataclass
class DeviceContext:
    device_uid: str
    email: str
    display_name: str
    sector_id: str
    status: str
    id_token: str
    refresh_token: str
    token_expires_in: Optional[int] = None


@dataclass
class ValidationResult:
    allowed: bool
    reason: Optional[str] = None
    token_data: Optional[Dict[str, Any]] = None
    booking_data: Optional[Dict[str, Any]] = None
    locker_data: Optional[Dict[str, Any]] = None


@dataclass
class LockerSession:
    request_id: str
    token_id: str
    booking_id: str
    locker_id: str
    sector_id: str
    device_uid: str

    phase: SessionPhase = SessionPhase.IDLE
    created_at: datetime = field(default_factory=datetime.utcnow)
    opened_at: Optional[datetime] = None
    signature_captured_at: Optional[datetime] = None
    close_requested_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    signature_path: Optional[str] = None

    weight_expected_grams: Optional[int] = None
    weight_measured_grams: Optional[int] = None
    weight_accepted: Optional[bool] = None


@dataclass
class SignatureResult:
    captured: bool
    signed_at: Optional[datetime] = None
    local_file_path: Optional[str] = None
    signer_role: Optional[str] = None
    valid: bool = False
    validation_reason: Optional[str] = None


class ControllerEventType(Enum):
    OPEN_ACK = "OPEN_ACK"
    OPEN_DENIED = "OPEN_DENIED"
    DOOR_OPEN = "DOOR_OPEN"
    DOOR_CLOSED = "DOOR_CLOSED"
    CLOSE_ACK = "CLOSE_ACK"
    WEIGHT_MEASURED = "WEIGHT_MEASURED"
    WEIGHT_OK = "WEIGHT_OK"
    WEIGHT_MISMATCH = "WEIGHT_MISMATCH"
    TAMPER = "TAMPER"
    HEARTBEAT = "HEARTBEAT"

@dataclass
class ControllerEvent:
    event_type: ControllerEventType
    locker_id: str
    request_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    received_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CloseGateResult:
    can_close: bool
    blocking_reasons: Optional[list[str]] = None


@dataclass
class LockerTelemetry:
	locker_id: str
	sector_id: str
	door_closed: bool
	tamper_flag: bool
	last_heatbeat_at: datetime
