from sqlalchemy.orm import Session

from app.models import EmailOutbox


def enqueue_email(db: Session, dedupe_key: str, to_email: str, to_name: str,
                  subject: str, text: str, html: str) -> None:
    """Adds a row to the session. The CALLER commits, so the email is queued atomically with the business change."""
    db.add(EmailOutbox(dedupe_key=dedupe_key, to_email=to_email, to_name=to_name,
                       subject=subject, text_body=text, html_body=html))
