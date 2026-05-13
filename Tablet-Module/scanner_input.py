from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional, TextIO
import queue
import sys
import threading
import time


class ScannerInputError(Exception):
    """Raised when scanner input cannot be read or is invalid."""


@dataclass(slots=True)
class ScanResult:
    raw_text: str
    normalized_text: str
    scanned_at: datetime


class ScannerInput:

    def __init__(
        self,
        input_stream: Optional[TextIO] = None,
        min_length: int = 1,
        max_length: int = 512,
        queue_size: int = 100,
        scan_cooldown_seconds: float = 2.0,
    ) -> None:
        self.input_stream = input_stream or sys.stdin
        self.min_length = min_length
        self.max_length = max_length
        self.scan_cooldown_seconds = scan_cooldown_seconds

        self._scan_queue: queue.Queue[ScanResult] = queue.Queue(maxsize=queue_size)
        self._error_queue: queue.Queue[Exception] = queue.Queue(maxsize=20)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._last_accepted_text: Optional[str] = None
        self._last_accepted_monotonic: Optional[float] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="scanner-input-thread",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout: float = 1.0) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    def get_scan(self, timeout: Optional[float] = None) -> Optional[ScanResult]:
        self._raise_pending_error()

        try:
            result = self._scan_queue.get(timeout=timeout)
            self._raise_pending_error()
            return result
        except queue.Empty:
            self._raise_pending_error()
            return None

    def get_scan_nowait(self) -> Optional[ScanResult]:
        return self.get_scan(timeout=0)

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                line = self.input_stream.readline()
            except Exception as exc:
                self._push_error(ScannerInputError(f"Scanner read failed: {exc}"))
                return

            if line == "":
                self._push_error(
                    ScannerInputError("Input stream closed while waiting for scan")
                )
                return

            raw_text = line.rstrip("\r\n")
            normalized_text = self._normalize(raw_text)

            if not normalized_text:
                continue

            if len(normalized_text) < self.min_length:
                continue

            if len(normalized_text) > self.max_length:
                continue

            now_mono = time.monotonic()

            if self._is_duplicate_within_cooldown(normalized_text, now_mono):
                continue

            result = ScanResult(
                raw_text=raw_text,
                normalized_text=normalized_text,
                scanned_at=datetime.now(UTC),
            )

            self._remember_accepted_scan(normalized_text, now_mono)

            try:
                self._scan_queue.put_nowait(result)
            except queue.Full:
                try:
                    _ = self._scan_queue.get_nowait()
                except queue.Empty:
                    pass

                try:
                    self._scan_queue.put_nowait(result)
                except queue.Full:
                    pass

    def _is_duplicate_within_cooldown(self, text: str, now_mono: float) -> bool:
        if self._last_accepted_text != text:
            return False

        if self._last_accepted_monotonic is None:
            return False

        delta = now_mono - self._last_accepted_monotonic
        return delta < self.scan_cooldown_seconds

    def _remember_accepted_scan(self, text: str, now_mono: float) -> None:
        self._last_accepted_text = text
        self._last_accepted_monotonic = now_mono

    def _push_error(self, exc: Exception) -> None:
        try:
            self._error_queue.put_nowait(exc)
        except queue.Full:
            pass

    def _raise_pending_error(self) -> None:
        try:
            exc = self._error_queue.get_nowait()
        except queue.Empty:
            return

        if isinstance(exc, ScannerInputError):
            raise exc
        raise ScannerInputError(str(exc)) from exc

    @staticmethod
    def _normalize(text: str) -> str:
        return text.strip()
