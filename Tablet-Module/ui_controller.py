from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UIState:
    name: str
    title: str
    subtitle: str
    updated_at: datetime


class UIController:
    """Display-only UI adapter with optional touchscreen Tk frontend."""

    def __init__(self, *, enable_tk: bool = False, fullscreen: bool = False) -> None:
        self._enable_tk = enable_tk
        self._fullscreen = fullscreen
        self._lock = threading.Lock()
        self._state = UIState("idle", "DropLock Tablet", "Scan QR to begin", datetime.now(timezone.utc))

        self._ui_queue: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=40)
        self._ui_thread: Optional[threading.Thread] = None

        if self._enable_tk:
            self._start_tk_thread()

    def show_idle(self) -> None:
        self._set_state("idle", "DropLock Tablet", "Scan QR to begin")

    def show_validating(self, *, token_id: str) -> None:
        self._set_state("validating", "Validating QR", f"Token: {token_id}")

    def show_denied(self, *, token_id: str, reason: str) -> None:
        self._set_state("denied", "Access denied", f"{reason} | {token_id}")

    def show_unlocking(self, *, session) -> None:
        self._set_state("unlocking", "Unlocking locker", f"Locker {session.locker_id}")

    def show_weight_wait(self, *, session, reason: str) -> None:
        self._set_state("waiting_weight", "Waiting for valid package weight", reason)

    def show_signature(self, *, session) -> None:
        self._set_state("waiting_signature", "Signature required", "Please sign on screen")

    def show_closing(self, *, session) -> None:
        self._set_state("closing", "Closing locker", "Please wait")

    def show_completed(self, *, session) -> None:
        self._set_state("completed", "Completed", "Thank you")

    def show_busy(self, *, token_id: str) -> None:
        self._set_state("busy", "System busy", "Another session is active")

    def show_error(self, *, reason: str) -> None:
        self._set_state("error", "Runtime error", reason)

    def show_signature_failed(self, *, reason: str | None) -> None:
        self._set_state("signature_failed", "Signature failed", reason or "Please retry")

    def show_close_blocked(self, *, blocking_reasons: list[str]) -> None:
        self._set_state("close_blocked", "Cannot close yet", ", ".join(blocking_reasons))

    def _set_state(self, name: str, title: str, subtitle: str) -> None:
        with self._lock:
            self._state = UIState(name, title, subtitle, datetime.now(timezone.utc))

        logger.info("UI state=%s title=%s subtitle=%s", name, title, subtitle)
        if self._enable_tk:
            self._push_ui_update(title, subtitle)

    def _push_ui_update(self, title: str, subtitle: str) -> None:
        try:
            self._ui_queue.put_nowait((title, subtitle))
        except queue.Full:
            try:
                _ = self._ui_queue.get_nowait()
                self._ui_queue.put_nowait((title, subtitle))
            except Exception:
                logger.warning("Failed to queue UI update")

    def _start_tk_thread(self) -> None:
        if self._ui_thread and self._ui_thread.is_alive():
            return

        self._ui_thread = threading.Thread(target=self._tk_loop, name="droplock-ui", daemon=True)
        self._ui_thread.start()

    def _tk_loop(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            logger.exception("Tkinter is not available on this system")
            return

        root = tk.Tk()
        root.title("DropLock Tablet")
        root.configure(bg="#0f172a")
        root.geometry("800x480")
        root.minsize(800, 480)
        if self._fullscreen:
            root.attributes("-fullscreen", True)

        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)

        frame = tk.Frame(root, bg="#0f172a", padx=30, pady=30)
        frame.grid(sticky="nsew")
        frame.grid_rowconfigure(0, weight=2)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        title_var = tk.StringVar(value=self._state.title)
        subtitle_var = tk.StringVar(value=self._state.subtitle)
        clock_var = tk.StringVar(value="")

        title_label = tk.Label(
            frame,
            textvariable=title_var,
            fg="#f8fafc",
            bg="#0f172a",
            font=("Helvetica", 42, "bold"),
            wraplength=740,
            justify="center",
        )
        title_label.grid(row=0, column=0, sticky="nsew", pady=(20, 10))

        subtitle_label = tk.Label(
            frame,
            textvariable=subtitle_var,
            fg="#cbd5e1",
            bg="#0f172a",
            font=("Helvetica", 24),
            wraplength=740,
            justify="center",
        )
        subtitle_label.grid(row=1, column=0, sticky="nsew", pady=8)

        badge = tk.Label(
            frame,
            text="DropLock Sector Hub",
            fg="#38bdf8",
            bg="#0f172a",
            font=("Helvetica", 18, "bold"),
        )
        badge.grid(row=2, column=0, sticky="n", pady=(6, 0))

        clock = tk.Label(
            frame,
            textvariable=clock_var,
            fg="#64748b",
            bg="#0f172a",
            font=("Helvetica", 14),
        )
        clock.grid(row=2, column=0, sticky="se")

        def poll_updates() -> None:
            while True:
                try:
                    title, subtitle = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                title_var.set(title)
                subtitle_var.set(subtitle)

            clock_var.set(datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
            root.after(200, poll_updates)

        poll_updates()
        root.mainloop()
