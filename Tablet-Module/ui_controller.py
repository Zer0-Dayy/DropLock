from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


STATE_COLORS = {
    "idle": "#0ea5e9",
    "validating": "#3b82f6",
    "unlocking": "#22c55e",
    "waiting_weight": "#f59e0b",
    "waiting_signature": "#a855f7",
    "closing": "#14b8a6",
    "completed": "#10b981",
    "busy": "#f97316",
    "denied": "#ef4444",
    "error": "#dc2626",
    "signature_failed": "#f43f5e",
    "close_blocked": "#f59e0b",
}


@dataclass(slots=True)
class UIState:
    name: str
    title: str
    subtitle: str
    updated_at: datetime


class UIController:
    """Display-only UI adapter with a full-screen touchscreen Tk frontend."""

    def __init__(self, *, enable_tk: bool = True, fullscreen: bool = True) -> None:
        self._enable_tk = enable_tk
        self._fullscreen = fullscreen
        self._lock = threading.Lock()
        self._state = UIState("idle", "DropLock Tablet", "Scan QR to begin", datetime.now(timezone.utc))

        self._ui_queue: "queue.Queue[UIState]" = queue.Queue(maxsize=40)
        self._ui_thread: Optional[threading.Thread] = None

        if self._enable_tk:
            self._start_tk_thread()

    def show_idle(self) -> None:
        self._set_state("idle", "Ready", "Please scan a QR code")

    def show_validating(self, *, token_id: str) -> None:
        self._set_state("validating", "Validating", f"Checking token {token_id}")

    def show_denied(self, *, token_id: str, reason: str) -> None:
        self._set_state("denied", "Access denied", f"{reason} • {token_id}")

    def show_unlocking(self, *, session) -> None:
        self._set_state("unlocking", "Unlocking locker", f"Locker {session.locker_id}")

    def show_weight_wait(self, *, session, reason: str) -> None:
        self._set_state("waiting_weight", "Weight verification", reason)

    def show_signature(self, *, session) -> None:
        self._set_state("waiting_signature", "Signature required", "Please sign on the screen")

    def show_closing(self, *, session) -> None:
        self._set_state("closing", "Closing locker", "Securing your package")

    def show_completed(self, *, session) -> None:
        self._set_state("completed", "Completed", "Operation finished successfully")

    def show_busy(self, *, token_id: str) -> None:
        self._set_state("busy", "System busy", "Another locker session is running")

    def show_error(self, *, reason: str) -> None:
        self._set_state("error", "System error", reason)

    def show_signature_failed(self, *, reason: str | None) -> None:
        self._set_state("signature_failed", "Signature failed", reason or "Please retry")

    def show_close_blocked(self, *, blocking_reasons: list[str]) -> None:
        self._set_state("close_blocked", "Cannot close yet", ", ".join(blocking_reasons))

    def _set_state(self, name: str, title: str, subtitle: str) -> None:
        state = UIState(name, title, subtitle, datetime.now(timezone.utc))
        with self._lock:
            self._state = state

        logger.info("UI state=%s title=%s subtitle=%s", name, title, subtitle)
        if self._enable_tk:
            self._push_ui_update(state)

    def _push_ui_update(self, state: UIState) -> None:
        try:
            self._ui_queue.put_nowait(state)
        except queue.Full:
            try:
                _ = self._ui_queue.get_nowait()
                self._ui_queue.put_nowait(state)
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
            from tkinter import ttk
        except Exception:
            logger.exception("Tkinter is not available on this system")
            return

        root = tk.Tk()
        root.title("DropLock Tablet")
        root.configure(bg="#020617")
        root.geometry("1024x600")
        root.minsize(900, 540)
        if self._fullscreen:
            root.attributes("-fullscreen", True)

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Drop.Horizontal.TProgressbar", troughcolor="#0f172a", background="#22d3ee", thickness=10)

        wrapper = tk.Frame(root, bg="#020617")
        wrapper.pack(fill="both", expand=True)

        header = tk.Frame(wrapper, bg="#0b1220", height=95)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        brand = tk.Label(
            header,
            text="DropLock",
            fg="#f8fafc",
            bg="#0b1220",
            font=("Helvetica", 34, "bold"),
        )
        brand.pack(side="left", padx=24, pady=16)

        clock_var = tk.StringVar(value="")
        clock = tk.Label(header, textvariable=clock_var, fg="#94a3b8", bg="#0b1220", font=("Helvetica", 18))
        clock.pack(side="right", padx=24, pady=16)

        body = tk.Frame(wrapper, bg="#020617", padx=32, pady=26)
        body.pack(fill="both", expand=True)

        title_var = tk.StringVar(value=self._state.title)
        subtitle_var = tk.StringVar(value=self._state.subtitle)
        state_var = tk.StringVar(value=self._state.name.upper())

        status_badge = tk.Label(
            body,
            textvariable=state_var,
            fg="#e2e8f0",
            bg=STATE_COLORS.get(self._state.name, "#0ea5e9"),
            font=("Helvetica", 16, "bold"),
            padx=16,
            pady=8,
        )
        status_badge.pack(anchor="w", pady=(0, 22))

        title_label = tk.Label(
            body,
            textvariable=title_var,
            fg="#f8fafc",
            bg="#020617",
            font=("Helvetica", 52, "bold"),
            anchor="w",
            justify="left",
            wraplength=920,
        )
        title_label.pack(fill="x")

        subtitle_label = tk.Label(
            body,
            textvariable=subtitle_var,
            fg="#cbd5e1",
            bg="#020617",
            font=("Helvetica", 28),
            anchor="w",
            justify="left",
            wraplength=920,
            pady=10,
        )
        subtitle_label.pack(fill="x")

        footer = tk.Frame(wrapper, bg="#0b1220", height=120)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        guidance = tk.Label(
            footer,
            text="Scanner active • Controller connected via MQTT • Firebase synchronized",
            fg="#93c5fd",
            bg="#0b1220",
            font=("Helvetica", 17),
        )
        guidance.pack(anchor="w", padx=24, pady=(18, 6))

        progress = ttk.Progressbar(footer, mode="indeterminate", style="Drop.Horizontal.TProgressbar")
        progress.pack(fill="x", padx=24, pady=(0, 16))
        progress.start(16)

        def poll_updates() -> None:
            while True:
                try:
                    state = self._ui_queue.get_nowait()
                except queue.Empty:
                    break

                title_var.set(state.title)
                subtitle_var.set(state.subtitle)
                state_var.set(state.name.upper())
                status_badge.configure(bg=STATE_COLORS.get(state.name, "#0ea5e9"))

            clock_var.set(datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
            root.after(150, poll_updates)

        poll_updates()
        root.mainloop()
