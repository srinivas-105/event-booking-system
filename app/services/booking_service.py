"""Ticket reservation.

optimized: ONE atomic conditional UPDATE   ->  UPDATE events SET available_tickets = available_tickets - :q
                                                WHERE id = :id AND available_tickets >= :q AND event_date > now()
           PostgreSQL takes a row lock for the UPDATE, so concurrent requests queue on that row and each one
           re-checks the condition against the latest committed value. rowcount==0 means sold out.
           The booking row and the email-outbox row are written in the same transaction.

baseline:  the naive "read, check in Python, write" version plus a blocking email send inside the request.
           It exists ONLY so the load test can show a measured before/after. It can oversell. Never use in production.
"""
import logging
import secrets
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Booking, Event, User
from app.services.email_service import send_email
from app.services.email_templates import booking_confirmation
from app.services.outbox import enqueue_email
from app.utils import as_utc, utcnow

logger = logging.getLogger("booking")


class EventNotFound(Exception): ...
class EventEnded(Exception): ...
class SoldOut(Exception): ...
class BookingNotFound(Exception): ...
class BookingNotCancellable(Exception): ...


def _new_reference() -> str:
    return "BK-" + secrets.token_hex(6).upper()  # 12 hex chars; the UNIQUE constraint is the safety net


def _build_booking(customer: User, event: Event, quantity: int) -> Booking:
    unit = Decimal(event.ticket_price)
    return Booking(
        reference=_new_reference(), customer_id=customer.id, event=event, quantity=quantity,
        unit_price=unit, total_price=unit * quantity, status="confirmed",
    )


def _raise_why_it_failed(db: Session, event_id: int) -> None:
    event = db.get(Event, event_id)
    if event is None:
        raise EventNotFound()
    if as_utc(event.event_date) <= utcnow():
        raise EventEnded()
    raise SoldOut()


def create_booking(db: Session, customer: User, event_id: int, quantity: int) -> Booking:
    now = utcnow()
    result = db.execute(
        update(Event)
        .where(Event.id == event_id, Event.available_tickets >= quantity, Event.event_date > now)
        .values(available_tickets=Event.available_tickets - quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        _raise_why_it_failed(db, event_id)

    # We still hold the row lock, so this read is consistent with our update.
    event = db.execute(
        select(Event).where(Event.id == event_id).execution_options(populate_existing=True)
    ).scalar_one()
    booking = _build_booking(customer, event, quantity)
    db.add(booking)

    subject, text, html = booking_confirmation(customer.name, event, booking)
    enqueue_email(db, f"booking-confirmation:{booking.reference}", customer.email, customer.name, subject, text, html)
    db.commit()  # tickets + booking + queued email become visible together, or not at all
    return booking


def create_booking_baseline(db: Session, customer: User, event_id: int, quantity: int) -> Booking:
    event = db.get(Event, event_id)  # 1) read
    if event is None:
        raise EventNotFound()
    if as_utc(event.event_date) <= utcnow():
        raise EventEnded()
    if event.available_tickets < quantity:  # 2) check in Python
        raise SoldOut()
    event.available_tickets = event.available_tickets - quantity  # 3) write an absolute value (lost-update race)
    booking = _build_booking(customer, event, quantity)
    db.add(booking)
    db.commit()

    # 4) blocking email call INSIDE the request: user waits for the provider
    subject, text, html = booking_confirmation(customer.name, event, booking)
    try:
        send_email(customer.email, customer.name, subject, text, html)
    except Exception:
        logger.exception("baseline inline email failed for %s", booking.reference)
    return booking


def cancel_booking(db: Session, customer: User, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None or booking.customer_id != customer.id:
        raise BookingNotFound()  # same answer for "not yours" so ids cannot be probed
    event = db.get(Event, booking.event_id)
    if booking.status != "confirmed" or as_utc(event.event_date) <= utcnow():
        raise BookingNotCancellable()
    res = db.execute(
        update(Booking).where(Booking.id == booking_id, Booking.status == "confirmed")
        .values(status="cancelled").execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:  # someone cancelled it concurrently
        db.rollback()
        raise BookingNotCancellable()
    db.execute(
        update(Event).where(Event.id == booking.event_id)
        .values(available_tickets=Event.available_tickets + booking.quantity)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(booking)
    return booking
