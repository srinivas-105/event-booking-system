
"""Send email through Gmail SMTP or Resend. Retries are owned by the outbox worker."""

import logging
import smtplib
import time
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from app.config import get_settings

logger = logging.getLogger("email")
settings = get_settings()

RESEND_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _send_gmail(
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> None:
    sender = settings.gmail_email.strip()
    password = settings.gmail_app_password.replace(" ", "").strip()

    if not sender or not password:
        raise EmailSendError(
            "GMAIL_EMAIL or GMAIL_APP_PASSWORD is not configured",
            permanent=True,
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text)

    if html:
        message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=settings.email_http_timeout) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(message)

    except smtplib.SMTPAuthenticationError as exc:
        raise EmailSendError(
            "Gmail authentication failed; check GMAIL_EMAIL and the App Password",
            permanent=True,
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailSendError(
            "Gmail refused the recipient address",
            permanent=True,
        ) from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise EmailSendError(
            f"Gmail SMTP error: {type(exc).__name__}"
        ) from exc


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    text: str,
    html: str,
    idempotency_key: str | None = None,
) -> None:
    provider = settings.email_provider.lower()

    if provider == "mock":
        # Used only for tests and load tests.
        if settings.email_mock_latency_ms:
            time.sleep(settings.email_mock_latency_ms / 1000)
        logger.info("mock email -> %s | %s", to_email, subject)
        return

    if provider == "gmail":
        _send_gmail(to_email, subject, text, html)
        logger.info("email sent through Gmail -> %s | %s", to_email, subject)
        return

    if provider != "resend":
        raise EmailSendError(
            f"Unsupported email provider: {provider}",
            permanent=True,
        )

    if not settings.resend_api_key:
        raise EmailSendError("RESEND_API_KEY is not configured", permanent=True)

    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}

    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": subject,
        "text": text,
        "html": html,
    }

    try:
        response = httpx.post(
            RESEND_URL,
            json=payload,
            headers=headers,
            timeout=settings.email_http_timeout,
        )
    except httpx.HTTPError as exc:
        raise EmailSendError(
            f"Resend network error: {type(exc).__name__}"
        ) from exc

    if response.status_code >= 400:
        permanent = (
            response.status_code < 500
            and response.status_code not in (408, 429)
        )
        raise EmailSendError(
            f"Resend returned {response.status_code}: {response.text[:200]}",
            permanent=permanent,
        )

    logger.info("email sent through Resend -> %s | %s", to_email, subject)
