from __future__ import annotations

import io
import logging
import smtplib
import threading
from email.message import EmailMessage
from typing import Optional


logger = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        from_email: str,
        use_tls: bool = True,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._from_email = from_email
        self._use_tls = use_tls

    def enabled(self) -> bool:
        return bool(self._smtp_host and self._from_email)

    def send_token_email_async(
        self,
        *,
        to_email: str,
        recipient_name: str,
        booking_id: str,
        token_id: str,
        purpose: str,
    ) -> None:
        if not self.enabled():
            logger.info("Email notifier disabled; skipping message to %s", to_email)
            return

        thread = threading.Thread(
            target=self._safe_send_token_email,
            kwargs={
                "to_email": to_email,
                "recipient_name": recipient_name,
                "booking_id": booking_id,
                "token_id": token_id,
                "purpose": purpose,
            },
            daemon=True,
            name="email-notifier",
        )
        thread.start()

    def _safe_send_token_email(self, **kwargs) -> None:
        try:
            self.send_token_email(**kwargs)
        except Exception:
            logger.exception("Failed to send token email")

    def send_token_email(
        self,
        *,
        to_email: str,
        recipient_name: str,
        booking_id: str,
        token_id: str,
        purpose: str,
    ) -> None:
        png_data = self._build_qr_png(token_id)

        subject = "DropLock QR code"
        action = "drop" if purpose == "COURIER_DROP" else "pickup"
        text = (
            f"Hello {recipient_name or 'customer'},\n\n"
            f"Your booking ({booking_id}) is ready for {action}.\n"
            f"Token ID: {token_id}\n"
            "Please present the attached QR code at the tablet scanner.\n"
        )

        message = EmailMessage()
        message["From"] = self._from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text)
        message.add_attachment(
            png_data,
            maintype="image",
            subtype="png",
            filename=f"droplock_{purpose.lower()}_{booking_id}.png",
        )

        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._smtp_username:
                smtp.login(self._smtp_username, self._smtp_password)
            smtp.send_message(message)

        logger.info("Token email sent to=%s booking_id=%s purpose=%s", to_email, booking_id, purpose)

    @staticmethod
    def _build_qr_png(token_id: str) -> bytes:
        try:
            import qrcode
        except Exception as exc:
            raise RuntimeError("qrcode package is required for QR image email attachments") from exc

        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(token_id)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
