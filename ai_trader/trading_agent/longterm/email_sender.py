"""SMTP email sender helpers for long-term trader notifications."""

from __future__ import annotations

from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import smtplib
from pathlib import Path
from typing import Callable

from longterm.capital_alert import CapitalNeededEmail


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool = False
    email_to: str = ""
    email_from: str = ""
    username: str = ""
    password: str = ""
    smtp_host: str = "smtp-relay.brevo.com"
    smtp_port: int = 587
    timeout_seconds: int = 20


@dataclass(frozen=True)
class EmailSendResult:
    sent: bool
    reason: str


class SmtpEmailSender:
    """Send prepared email payloads through an SMTP provider such as Brevo."""

    def __init__(self, *, smtp_factory: Callable | None = None):
        self.smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, email: CapitalNeededEmail, settings: EmailSettings) -> EmailSendResult:
        if not settings.enabled:
            return EmailSendResult(False, "Email notifications disabled.")
        if not email.should_send:
            return EmailSendResult(False, "Email payload is not sendable.")

        to_addr = (settings.email_to or email.recipient_email or "").strip()
        username = settings.username.strip()
        password = settings.password.strip()
        from_addr = (settings.email_from or username).strip()
        host = settings.smtp_host.strip() or "smtp-relay.brevo.com"
        port = int(settings.smtp_port or 587)

        if not to_addr or not from_addr or not username or not password:
            return EmailSendResult(False, "Email settings incomplete.")

        message = MIMEMultipart("alternative")
        message["Subject"] = email.subject
        message["From"] = from_addr
        message["To"] = to_addr
        message.attach(MIMEText(email.text_body, "plain", "utf-8"))
        if email.html_body:
            message.attach(MIMEText(email.html_body, "html", "utf-8"))

        with self.smtp_factory(host, port, timeout=settings.timeout_seconds) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(username, password)
            smtp.sendmail(from_addr, [to_addr], message.as_string())

        return EmailSendResult(True, f"Email sent to {to_addr}.")


def load_email_settings(path: str | Path | None = None) -> EmailSettings:
    """Load Brevo-style SMTP settings from a JSON config file."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "email_notifications.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return EmailSettings(
        enabled=bool(data.get("email_notifications", False)),
        email_to=str(data.get("email_to", "")),
        email_from=str(data.get("email_from", "")),
        username=str(data.get("email_username", "")),
        password=str(data.get("email_password", "")),
        smtp_host=str(data.get("email_smtp_host", "smtp-relay.brevo.com")),
        smtp_port=int(data.get("email_smtp_port", 587)),
    )
