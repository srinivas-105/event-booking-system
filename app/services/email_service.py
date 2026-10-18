"""Sends one email through the provider. No retry logic here: the outbox worker owns retries."""
import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger("email")
settings = get_settings()

RESEND_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent  # True = retrying will not help (bad key, unverified domain, bad address)


def send_email(to_email: str, to_name: str, subject: str, text: str, html: str,
               idempotency_key: str | None = None) -> None:
    if settings.email_provider == "mock":
        # Used ONLY for unit tests and load tests so we do not spam a real inbox / hit provider rate limits.
        if settings.email_mock_latency_ms:
            time.sleep(settings.email_mock_latency_ms / 1000)
        logger.info("mock email -> %s | %s", to_email, subject)
        return

    if not settings.resend_api_key:
        raise EmailSendError("RESEND_API_KEY is not configured", permanent=True)

    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    if idempotency_key:
        # Resend de-duplicates requests with the same key, so a retry after a crash cannot double-send.
        headers["Idempotency-Key"] = idempotency_key
    payload = {"from": settings.email_from, "to": [to_email], "subject": subject, "text": text, "html": html}

    try:
        resp = httpx.post(RESEND_URL, json=payload, headers=headers, timeout=settings.email_http_timeout)
    except httpx.HTTPError as exc:  # timeout, DNS, connection reset ...
        raise EmailSendError(f"network error: {type(exc).__name__}") from exc

    if resp.status_code >= 400:
        # 4xx (except 408/429) means our request is wrong -> retrying is pointless.
        permanent = resp.status_code < 500 and resp.status_code not in (408, 429)
        raise EmailSendError(f"provider returned {resp.status_code}: {resp.text[:200]}", permanent=permanent)
    logger.info("email sent -> %s | %s", to_email, subject)
