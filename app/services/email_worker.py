"""Background worker that drains the email_outbox table.

* Claims rows with FOR UPDATE SKIP LOCKED (PostgreSQL), so several workers never grab the same row.
* A claimed row gets a lease (locked_until). If the process dies mid-send the lease expires and the row is retried.
* Failures are recorded (attempts, last_error) and retried with exponential backoff, then marked 'failed'.
* The dedupe_key is sent as the provider Idempotency-Key, so a retry cannot create a duplicate email.
"""
import logging
import threading
from datetime import timedelta

from sqlalchemy import and_, or_, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import EmailOutbox
from app.services.email_service import EmailSendError, send_email
from app.utils import utcnow

logger = logging.getLogger("email.worker")
settings = get_settings()

LEASE = timedelta(minutes=2)


def backoff_seconds(attempts: int) -> int:
    return min(5 * 2 ** attempts, 600)  # 10s, 20s, 40s, ... capped at 10 min


def claim_batch(limit: int) -> list[int]:
    now = utcnow()
    with SessionLocal() as db:
        rows = db.scalars(
            select(EmailOutbox)
            .where(or_(
                and_(EmailOutbox.status == "pending", EmailOutbox.next_attempt_at <= now),
                and_(EmailOutbox.status == "processing", EmailOutbox.locked_until < now),
            ))
            .order_by(EmailOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.status = "processing"
            row.locked_until = now + LEASE
        db.commit()
        return [r.id for r in rows]


def deliver(outbox_id: int) -> None:
    with SessionLocal() as db:
        row = db.get(EmailOutbox, outbox_id)
        if row is None or row.status == "sent":
            return
        try:
            send_email(row.to_email, row.to_name, row.subject, row.text_body, row.html_body,
                       idempotency_key=row.dedupe_key)
        except EmailSendError as exc:
            row.attempts += 1
            row.last_error = str(exc)[:500]
            if exc.permanent or row.attempts >= settings.email_max_attempts:
                row.status = "failed"
                logger.error("email %s failed permanently after %s attempt(s): %s", row.id, row.attempts, exc)
            else:
                row.status = "pending"
                row.next_attempt_at = utcnow() + timedelta(seconds=backoff_seconds(row.attempts))
                logger.warning("email %s attempt %s failed, will retry: %s", row.id, row.attempts, exc)
        else:
            row.status = "sent"
            row.sent_at = utcnow()
            row.attempts += 1
            row.last_error = None
        row.locked_until = None
        db.commit()


def process_batch() -> int:
    ids = claim_batch(settings.email_batch_size)
    for outbox_id in ids:
        try:
            deliver(outbox_id)
        except Exception:  # never let one bad row kill the worker
            logger.exception("unexpected error delivering outbox row %s", outbox_id)
    return len(ids)


class EmailWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="email-worker", daemon=True)
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info("email worker started")
        while not self._stop_event.is_set():
            try:
                processed = process_batch()
            except Exception:
                logger.exception("email worker loop error")
                processed = 0
            if processed == 0:
                self._stop_event.wait(settings.email_poll_seconds)
        logger.info("email worker stopped")

    def stop(self) -> None:
        self._stop_event.set()
