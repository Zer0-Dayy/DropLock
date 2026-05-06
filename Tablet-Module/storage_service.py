from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from config import SIGNATURE_BASE_PATH

@dataclass(frozen=True, slots=True)
class StoredFile:
    path: Path
    created_at: datetime
    size_bytes: int


class StorageService:

    def __init__(
        self,
        base_dir: str | Path = SIGNATURE_BASE_PATH,
        signatures_dir_name: str = "signatures",
        sessions_dir_name: str = "sessions",
    ) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._signatures_dir = self._base_dir / signatures_dir_name
        self._sessions_dir = self._base_dir / sessions_dir_name

    # ------------------------------------------------------------------
    # Public directory helpers
    # ------------------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def signatures_dir(self) -> Path:
        return self._signatures_dir

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    def ensure_base_dirs(self) -> None:
        """
        Create all root storage directories if they do not already exist.
        """
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._signatures_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def ensure_session_dir(
        self,
        *,
        sector_id: str,
        locker_id: str,
        request_id: str,
    ) -> Path:
        self._validate_required_string(sector_id, "sector_id")
        self._validate_required_string(locker_id, "locker_id")
        self._validate_required_string(request_id, "request_id")

        session_dir = (
            self._sessions_dir
            / self._safe_name(sector_id)
            / self._safe_name(locker_id)
            / self._safe_name(request_id)
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    # ------------------------------------------------------------------
    # Signature path helpers
    # ------------------------------------------------------------------

    def build_signature_path(
        self,
        *,
        sector_id: str,
        locker_id: str,
        request_id: str,
        signer_role: str,
        signed_at: Optional[datetime] = None,
        filename_prefix: str = "signature",
    ) -> Path:
        self._validate_required_string(signer_role, "signer_role")
        self._validate_required_string(filename_prefix, "filename_prefix")

        session_dir = self.ensure_session_dir(
            sector_id=sector_id,
            locker_id=locker_id,
            request_id=request_id,
        )

        ts = signed_at or datetime.now(timezone.utc)
        ts_str = ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        filename = (
            f"{self._safe_name(filename_prefix)}_"
            f"{self._safe_name(signer_role)}_"
            f"{ts_str}.png"
        )

        return session_dir / filename

    # ------------------------------------------------------------------
    # File write helpers
    # ------------------------------------------------------------------

    def save_bytes(
        self,
        *,
        data: bytes,
        path: str | Path,
        overwrite: bool = False,
    ) -> StoredFile:
        if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
            raise ValueError("data must be non-empty bytes")

        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not overwrite:
            raise FileExistsError(f"Target file already exists: {target}")

        payload = bytes(data)
        target.write_bytes(payload)

        return StoredFile(
            path=target,
            created_at=datetime.now(timezone.utc),
            size_bytes=len(payload),
        )

    def save_signature_png(
        self,
        *,
        png_bytes: bytes,
        sector_id: str,
        locker_id: str,
        request_id: str,
        signer_role: str,
        signed_at: Optional[datetime] = None,
        overwrite: bool = False,
    ) -> StoredFile:
        path = self.build_signature_path(
            sector_id=sector_id,
            locker_id=locker_id,
            request_id=request_id,
            signer_role=signer_role,
            signed_at=signed_at,
            filename_prefix="signature",
        )
        return self.save_bytes(
            data=png_bytes,
            path=path,
            overwrite=overwrite,
        )

    # ------------------------------------------------------------------
    # Read / utility helpers
    # ------------------------------------------------------------------

    def file_exists(self, path: str | Path) -> bool:
        return Path(path).expanduser().resolve().exists()

    def get_file_size(self, path: str | Path) -> int:
        target = Path(path).expanduser().resolve()
        return target.stat().st_size

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_required_string(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = value.strip().replace(" ", "_")
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned)
        cleaned = cleaned.strip("._-")

        if not cleaned:
            return "unnamed"

        return cleaned
