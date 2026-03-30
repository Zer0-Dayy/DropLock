from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from session_models import LockerSession, SignatureResult
from storage_service import StorageService


@dataclass(slots=True)
class _StrokeStats:
    stroke_count: int = 0
    point_count: int = 0
    total_path_length: float = 0.0

    def reset(self) -> None:
        self.stroke_count = 0
        self.point_count = 0
        self.total_path_length = 0.0


class SignatureCapture:

    def __init__(
        self,
        storage_service: StorageService,
        canvas_width: int = 800,
        canvas_height: int = 480,
        background_color: str = "white",
        pen_color: str = "black",
        pen_width: int = 3,
        min_strokes: int = 1,
        min_points: int = 15,
        min_path_length_px: float = 120.0,
        fullscreen: bool = True,
        title: str = "DropLock Signature Capture",
    ) -> None:
        self._storage_service = storage_service
        self._canvas_width = canvas_width
        self._canvas_height = canvas_height
        self._background_color = background_color
        self._pen_color = pen_color
        self._pen_width = pen_width
        self._min_strokes = min_strokes
        self._min_points = min_points
        self._min_path_length_px = min_path_length_px
        self._fullscreen = fullscreen
        self._title = title

        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._status_var: Optional[tk.StringVar] = None

        self._image: Optional[Image.Image] = None
        self._draw: Optional[ImageDraw.ImageDraw] = None

        self._stats = _StrokeStats()
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None

        self._captured_result: Optional[SignatureResult] = None
        self._active_session: Optional[LockerSession] = None
        self._active_signer_role: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture_signature(
        self,
        *,
        session: LockerSession,
        signer_role: str,
        prompt_text: Optional[str] = None,
    ) -> SignatureResult:
        """
        Open a blocking signature screen, save the PNG if valid, and return SignatureResult.
        """
        if not signer_role or not signer_role.strip():
            raise ValueError("signer_role must be a non-empty string")

        if not session.request_id:
            raise ValueError("LockerSession.request_id is required")
        if not session.sector_id:
            raise ValueError("LockerSession.sector_id is required")
        if not session.locker_id:
            raise ValueError("LockerSession.locker_id is required")

        self._active_session = session
        self._active_signer_role = signer_role.strip()

        signed_at = None
        self._captured_result = None

        self._build_ui(prompt_text=prompt_text)
        self._reset_signature_surface()

        assert self._root is not None
        self._root.mainloop()

        # Window closed without an explicit confirm/cancel path
        if self._captured_result is None:
            return SignatureResult(
                captured=False,
                signed_at=signed_at,
                local_file_path=None,
                signer_role=self._active_signer_role,
                valid=False,
                validation_reason="SIGNATURE_CAPTURE_CANCELLED",
            )

        return self._captured_result

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self, prompt_text: Optional[str]) -> None:
        self._root = tk.Tk()
        self._root.title(self._title)
        self._root.configure(bg="#f3f4f6")
        self._root.protocol("WM_DELETE_WINDOW", self._cancel_capture)

        if self._fullscreen:
            self._root.attributes("-fullscreen", True)

        self._root.rowconfigure(1, weight=1)
        self._root.columnconfigure(0, weight=1)

        header_text = prompt_text or "Please sign below before closing the locker."

        header = tk.Label(
            self._root,
            text=header_text,
            font=("Arial", 18, "bold"),
            bg="#f3f4f6",
            fg="#111827",
            pady=12,
        )
        header.grid(row=0, column=0, sticky="ew")

        helper = tk.Label(
            self._root,
            text="Use a finger or stylus. Tap Confirm when finished.",
            font=("Arial", 12),
            bg="#f3f4f6",
            fg="#4b5563",
            pady=0,
        )
        helper.grid(row=1, column=0, sticky="ew")

        center_frame = tk.Frame(self._root, bg="#f3f4f6", padx=20, pady=10)
        center_frame.grid(row=2, column=0, sticky="nsew")
        center_frame.rowconfigure(0, weight=1)
        center_frame.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            center_frame,
            width=self._canvas_width,
            height=self._canvas_height,
            bg=self._background_color,
            highlightthickness=2,
            highlightbackground="#9ca3af",
            relief="flat",
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._canvas.bind("<ButtonPress-1>", self._on_pen_down)
        self._canvas.bind("<B1-Motion>", self._on_pen_move)
        self._canvas.bind("<ButtonRelease-1>", self._on_pen_up)

        footer = tk.Frame(self._root, bg="#f3f4f6", padx=20, pady=16)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self._status_var = tk.StringVar(value="Sign using your finger or stylus.")
        status_label = tk.Label(
            footer,
            textvariable=self._status_var,
            font=("Arial", 12),
            bg="#f3f4f6",
            fg="#374151",
            anchor="w",
        )
        status_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))

        clear_btn = tk.Button(
            footer,
            text="Clear",
            width=12,
            height=2,
            bg="#e5e7eb",
            activebackground="#d1d5db",
            command=self._clear_signature,
        )
        clear_btn.grid(row=1, column=0, padx=8)

        cancel_btn = tk.Button(
            footer,
            text="Cancel",
            width=12,
            height=2,
            bg="#fee2e2",
            activebackground="#fecaca",
            command=self._cancel_capture,
        )
        cancel_btn.grid(row=1, column=1, padx=8)

        confirm_btn = tk.Button(
            footer,
            text="Confirm & Close",
            width=12,
            height=2,
            bg="#dcfce7",
            activebackground="#bbf7d0",
            command=self._confirm_signature,
        )
        confirm_btn.grid(row=1, column=2, padx=8)

        self._root.bind("<Escape>", self._on_escape)

    def _reset_signature_surface(self) -> None:
        assert self._canvas is not None

        self._stats.reset()
        self._last_x = None
        self._last_y = None

        self._canvas.delete("all")
        self._canvas.create_text(
            self._canvas_width // 2,
            self._canvas_height // 2,
            text="Sign here",
            fill="#d1d5db",
            font=("Arial", 28, "italic"),
        )

        self._image = Image.new(
            "RGB",
            (self._canvas_width, self._canvas_height),
            self._background_color,
        )
        self._draw = ImageDraw.Draw(self._image)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_pen_down(self, event: tk.Event) -> None:
        assert self._canvas is not None

        # Remove placeholder text on first contact
        self._canvas.delete("all")

        self._stats.stroke_count += 1
        self._stats.point_count += 1

        self._last_x = int(event.x)
        self._last_y = int(event.y)

        # Small dot for taps
        self._draw_point(self._last_x, self._last_y)

        self._set_status("Signature in progress...")

    def _on_pen_move(self, event: tk.Event) -> None:
        if self._last_x is None or self._last_y is None:
            return

        x = int(event.x)
        y = int(event.y)

        self._draw_segment(self._last_x, self._last_y, x, y)

        self._stats.point_count += 1
        self._stats.total_path_length += math.dist((self._last_x, self._last_y), (x, y))

        self._last_x = x
        self._last_y = y

    def _on_pen_up(self, event: tk.Event) -> None:
        self._last_x = None
        self._last_y = None
        self._set_status(
            f"Captured {self._stats.stroke_count} stroke(s), "
            f"{self._stats.point_count} point(s)."
        )

    def _on_escape(self, event: tk.Event) -> None:
        self._cancel_capture()

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_point(self, x: int, y: int) -> None:
        assert self._canvas is not None
        assert self._draw is not None

        r = max(1, self._pen_width // 2)

        self._canvas.create_oval(
            x - r,
            y - r,
            x + r,
            y + r,
            fill=self._pen_color,
            outline=self._pen_color,
        )

        self._draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=self._pen_color,
            outline=self._pen_color,
        )

    def _draw_segment(self, x1: int, y1: int, x2: int, y2: int) -> None:
        assert self._canvas is not None
        assert self._draw is not None

        self._canvas.create_line(
            x1,
            y1,
            x2,
            y2,
            fill=self._pen_color,
            width=self._pen_width,
            capstyle=tk.ROUND,
            smooth=True,
        )

        self._draw.line(
            (x1, y1, x2, y2),
            fill=self._pen_color,
            width=self._pen_width,
        )

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def _clear_signature(self) -> None:
        self._reset_signature_surface()
        self._set_status("Signature cleared. Please sign again.")

    def _cancel_capture(self) -> None:
        self._captured_result = SignatureResult(
            captured=False,
            signed_at=None,
            local_file_path=None,
            signer_role=self._active_signer_role,
            valid=False,
            validation_reason="SIGNATURE_CAPTURE_CANCELLED",
        )
        self._close_window()

    def _confirm_signature(self) -> None:
        validation_reason = self._validate_signature()

        if validation_reason != "OK":
            self._set_status(validation_reason.replace("_", " "))
            self._captured_result = SignatureResult(
                captured=False,
                signed_at=None,
                local_file_path=None,
                signer_role=self._active_signer_role,
                valid=False,
                validation_reason=validation_reason,
            )
            return

        assert self._active_session is not None
        assert self._active_signer_role is not None
        assert self._image is not None

        signed_at = datetime.now(timezone.utc)

        png_bytes = self._image_to_png_bytes(self._image)

        stored_file = self._storage_service.save_signature_png(
            png_bytes=png_bytes,
            sector_id=self._active_session.sector_id,
            locker_id=self._active_session.locker_id,
            request_id=self._active_session.request_id,
            signer_role=self._active_signer_role,
            signed_at=signed_at,
            overwrite=False,
        )

        self._captured_result = SignatureResult(
            captured=True,
            signed_at=signed_at,
            local_file_path=str(stored_file.path),
            signer_role=self._active_signer_role,
            valid=True,
            validation_reason="OK",
        )
        self._close_window()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_signature(self) -> str:
        if self._stats.stroke_count < self._min_strokes:
            return "SIGNATURE_TOO_FEW_STROKES"

        if self._stats.point_count < self._min_points:
            return "SIGNATURE_TOO_FEW_POINTS"

        if self._stats.total_path_length < self._min_path_length_px:
            return "SIGNATURE_TOO_SHORT"

        return "OK"

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        if self._status_var is not None:
            self._status_var.set(text)

    def _close_window(self) -> None:
        if self._root is not None:
            try:
                self._root.quit()
                self._root.destroy()
            finally:
                self._root = None
                self._canvas = None
                self._status_var = None
                self._image = None
                self._draw = None

    @staticmethod
    def _image_to_png_bytes(image: Image.Image) -> bytes:
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
