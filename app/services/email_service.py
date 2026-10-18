
"""Send emails through Gmail API, Gmail SMTP, or Resend."""

import base64
import logging
import smtplib
import time
from email.message import EmailMessage

import httpx

from app.config import get_settings

logger = logging.getLogger("email")
settings = get_settings()

RESEND_URL = "https://api.resend.com/emails"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class EmailSendError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _send_gmail_api(
    to_email: str,
    subject: str,
    text: str,
    html: str,
    idempotency_key: str | None = None,
) -> None:
    client_id = settings.gmail_client_id.strip()
    client_secret = settings.gmail_client_secret.strip()
    refresh_token = settings.gmail_refresh_token.strip()
    sender = settings.gmail_sender.strip()

    if not all((client_id, client_secret, refresh_token, sender)):
        raise EmailSendError(
            "Gmail API credentials are incomplete; configure "
            "GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
            "GMAIL_REFRESH_TOKEN, and GMAIL_SENDER",
            permanent=True,
        )

    try:
        token_response = httpx.post(
            GMAIL_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=settings.email_http_timeout,
        )

        if token_response.status_code >= 400:
            logger.error(
                "Gmail OAuth token request failed: HTTP %s",
                token_response.status_code,
            )
            raise EmailSendError(
                f"Gmail OAuth token request failed: HTTP "
                f"{token_response.status_code}",
                permanent=token_response.status_code in (400, 401, 403),
            )

        access_token = token_response.json().get("access_token")
        if not access_token:
            raise EmailSendError("Gmail OAuth response contained no access token")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text)

        if html:
            message.add_alternative(html, subtype="html")

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        response = httpx.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw_message},
            timeout=settings.email_http_timeout,
        )

        if response.status_code >= 400:
            logger.error(
                "Gmail API send failed: HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )
            raise EmailSendError(
                f"Gmail API send failed: HTTP {response.status_code}: "
                f"{response.text[:200]}",
                permanent=response.status_code in (400, 401, 403, 404),
            )

        logger.info("Email accepted by Gmail API -> %s | %s", to_email, subject)

    except httpx.HTTPError as exc:
        logger.exception("Gmail API network request failed")
        raise EmailSendError(
            f"Gmail API network error: {type(exc).__name__}"
        ) from exc


def _send_gmail_smtp(
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
        with smtplib.SMTP(
            "smtp.gmail.com",
            587,
            timeout=settings.email_http_timeout,
        ) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.send_message(message)

    except smtplib.SMTPAuthenticationError as exc:
        raise EmailSendError(
            "Gmail SMTP authentication failed",
            permanent=True,
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailSendError(
            "Gmail refused the recipient address",
            permanent=True,
        ) from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.exception("Gmail SMTP delivery failed")
        raise EmailSendError(
            f"Gmail SMTP error: {type(exc).__name__}: {exc}"
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
        if settings.email_mock_latency_ms:
            time.sleep(settings.email_mock_latency_ms / 1000)
        logger.info("Mock email -> %s | %s", to_email, subject)
        return

    if provider == "gmail_api":
        _send_gmail_api(to_email, subject, text, html, idempotency_key)
        return

    if provider == "gmail":
        _send_gmail_smtp(to_email, subject, text, html)
        logger.info("Email sent through Gmail SMTP -> %s | %s", to_email, subject)
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
        logger.exception("Resend network request failed")
        raise EmailSendError(
            f"Resend network error: {type(exc).__name__}"
        ) from exc

    if response.status_code >= 400:
        permanent = (
            response.status_code < 500
            and response.status_code not in (408, 429)
        )
        logger.error(
            "Resend returned HTTP %s: %s",
            response.status_code,
            response.text[:200],
        )
        raise EmailSendError(
            f"Resend returned {response.status_code}: {response.text[:200]}",
            permanent=permanent,
        )

    logger.info("Email accepted by Resend -> %s | %s", to_email, subject)
