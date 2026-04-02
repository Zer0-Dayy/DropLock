import unittest
from unittest.mock import patch

from email_notifier import EmailNotifier


class _FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        self.sent = message


class EmailNotifierFallbackTests(unittest.TestCase):
    def test_send_without_qr_attachment_when_qrcode_unavailable(self):
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            from_email="noreply@example.com",
            use_tls=True,
        )

        fake = _FakeSMTP()
        with patch("email_notifier.smtplib.SMTP", return_value=fake), patch.object(
            EmailNotifier,
            "_build_qr_png",
            side_effect=RuntimeError("qrcode missing"),
        ):
            notifier.send_token_email(
                to_email="recipient@example.com",
                recipient_name="Recipient",
                booking_id="book-1",
                token_id="tok-1",
                purpose="USER_PICKUP",
            )

        self.assertIsNotNone(fake.sent)
        self.assertEqual(len(list(fake.sent.iter_attachments())), 0)

    def test_send_tamper_alert_email(self):
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            from_email="noreply@example.com",
            use_tls=True,
        )
        fake = _FakeSMTP()

        with patch("email_notifier.smtplib.SMTP", return_value=fake):
            notifier.send_tamper_alert_email(
                to_email="admin@example.com",
                recipient_name="Sector Admin",
                sector_id="S1",
                locker_id="L3",
                detected_at_ms=1738352110000,
            )

        self.assertIsNotNone(fake.sent)
        self.assertIn("EMERGENCY", fake.sent["Subject"])


if __name__ == "__main__":
    unittest.main()
