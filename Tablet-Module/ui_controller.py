from __future__ import annotations

import logging
import multiprocessing as mp
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty

logger = logging.getLogger(__name__)


STATE_COLORS = {
    "idle": "#0ea5e9",
    "processing_request": "#3b82f6",
    "validating": "#3b82f6",
    "unlocking": "#22c55e",
    "locker_open": "#16a34a",
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
    updated_at: str


class UIController:
    """Display-only UI adapter with optional dedicated UI process."""

    def __init__(self, *, enable_tk: bool = True, fullscreen: bool = True) -> None:
        self._enable_tk = enable_tk
        self._fullscreen = fullscreen
        self._state = UIState("idle", "DropLock Tablet", "Scan QR to begin", self._now_iso())

        self._ui_queue: mp.Queue[dict] | None = None
        self._ui_process: mp.Process | None = None

        if self._enable_tk:
            self._start_ui_process()

    def show_idle(self) -> None:
        self._set_state("idle", "Ready", "Please scan a QR code")

    def show_processing_request(self, *, token_id: str) -> None:
        self._set_state("processing_request", "Processing Request", f"Checking token {token_id}")

    def show_validating(self, *, token_id: str) -> None:
        # Backward-compatible alias for older callers.
        self.show_processing_request(token_id=token_id)

    def show_denied(self, *, token_id: str, reason: str) -> None:
        self._set_state("denied", "Request Denied", f"{reason} • {token_id}")

    def show_unlocking(self, *, session) -> None:
        self._set_state("unlocking", "Unlocking locker", f"Locker {session.locker_id}")

    def show_locker_open(self, *, session) -> None:
        self._set_state("locker_open", f"Locker {session.locker_id} Open", "Proceed with your request")

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
        self._state = UIState(name, title, subtitle, self._now_iso())
        logger.info("UI state=%s title=%s subtitle=%s", name, title, subtitle)
        self._push_state(self._state)

    def _start_ui_process(self) -> None:
        if self._ui_process is not None and self._ui_process.is_alive():
            return

        self._ui_queue = mp.Queue(maxsize=100)
        self._ui_process = mp.Process(
            target=_run_ui_process,
            args=(self._ui_queue, self._fullscreen),
            daemon=True,
            name="droplock-ui",
        )
        self._ui_process.start()
        self._push_state(self._state)

    def _push_state(self, state: UIState) -> None:
        if not self._enable_tk or self._ui_queue is None:
            return

        payload = {
            "name": state.name,
            "title": state.title,
            "subtitle": state.subtitle,
            "updated_at": state.updated_at,
        }
        try:
            self._ui_queue.put_nowait(payload)
        except Exception:
            logger.warning("UI queue is full or unavailable; dropping update")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


def _run_ui_process(ui_queue: mp.Queue, fullscreen: bool) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        logger.exception("Tkinter is not available; UI process exiting")
        return

    root = tk.Tk()
    root.title("DropLock Tablet")
    root.configure(bg="#020617")
    root.geometry("480x320")
    root.minsize(460, 300)
    if fullscreen:
        root.attributes("-fullscreen", True)

    try:
        root.focusmodel("passive")
    except Exception:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Drop.Horizontal.TProgressbar", troughcolor="#0f172a", background="#22d3ee", thickness=12)

    wrapper = tk.Frame(root, bg="#020617")
    wrapper.pack(fill="both", expand=True)

    header = tk.Frame(wrapper, bg="#0b1220", height=52)
    header.pack(fill="x")
    header.pack_propagate(False)

    brand = tk.Label(header, text="DropLock", fg="#f8fafc", bg="#0b1220", font=("Helvetica", 18, "bold"))
    brand.pack(side="left", padx=10, pady=8)

    subtitle_small_var = tk.StringVar(value="Sector Hub")
    subtitle_small = tk.Label(header, textvariable=subtitle_small_var, fg="#60a5fa", bg="#0b1220", font=("Helvetica", 10))
    subtitle_small.pack(side="left", padx=(0, 8), pady=10)

    clock_var = tk.StringVar(value="")
    clock = tk.Label(header, textvariable=clock_var, fg="#94a3b8", bg="#0b1220", font=("Helvetica", 10))
    clock.pack(side="right", padx=10, pady=8)

    body = tk.Frame(wrapper, bg="#020617", padx=12, pady=8)
    body.pack(fill="both", expand=True)

    state_var = tk.StringVar(value="IDLE")
    title_var = tk.StringVar(value="Ready")
    msg_var = tk.StringVar(value="Please scan a QR code")
    updated_var = tk.StringVar(value="")

    status_badge = tk.Label(body, textvariable=state_var, fg="#e2e8f0", bg=STATE_COLORS["idle"], font=("Helvetica", 10, "bold"), padx=8, pady=3)
    status_badge.pack(anchor="w", pady=(2, 6))

    title_label = tk.Label(body, textvariable=title_var, fg="#f8fafc", bg="#020617", font=("Helvetica", 20, "bold"), anchor="w", justify="left", wraplength=430)
    title_label.pack(fill="x")

    msg_label = tk.Label(body, textvariable=msg_var, fg="#cbd5e1", bg="#020617", font=("Helvetica", 12), anchor="w", justify="left", wraplength=430, pady=4)
    msg_label.pack(fill="x")

    last_update = tk.Label(body, textvariable=updated_var, fg="#64748b", bg="#020617", font=("Helvetica", 8), anchor="w")
    last_update.pack(fill="x", pady=(3, 0))

    footer = tk.Frame(wrapper, bg="#0b1220", height=58)
    footer.pack(fill="x")
    footer.pack_propagate(False)

    guidance = tk.Label(
        footer,
        text="Scanner active • Controller online via MQTT • Firebase synchronized",
        fg="#93c5fd",
        bg="#0b1220",
        font=("Helvetica", 9),
    )
    guidance.pack(anchor="w", padx=10, pady=(6, 4))

    progress = ttk.Progressbar(footer, mode="indeterminate", style="Drop.Horizontal.TProgressbar")
    progress.pack(fill="x", padx=10, pady=(0, 6))
    progress.start(30)

    def poll() -> None:
        while True:
            try:
                payload = ui_queue.get_nowait()
            except Empty:
                break

            state_name = payload.get("name", "idle")
            state_var.set(state_name.upper())
            title_var.set(payload.get("title", "Ready"))
            msg_var.set(payload.get("subtitle", ""))
            updated_var.set(f"Last update: {payload.get('updated_at', '')}")
            status_badge.configure(bg=STATE_COLORS.get(state_name, "#0ea5e9"))

        clock_var.set(datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
        root.after(150, poll)

    poll()
    root.mainloop()
