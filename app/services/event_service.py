import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Booking, Event, User
from app.schemas import EventUpdate
from app.services.email_templates import event_updated
from app.services.outbox import enqueue_email
from app.utils import as_utc, fmt_dt, fmt_money


def get_owned_event(db: Session, event_id: int, organizer: User, lock: bool = False) -> Event:
    stmt = select(Event).where(Event.id == event_id)
    if lock:
        stmt = stmt.with_for_update()  # serialises with concurrent bookings on this event row
    event = db.execute(stmt).scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.organizer_id != organizer.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage your own events")
    return event


def update_event(db: Session, event_id: int, organizer: User, data: EventUpdate) -> Event:
    event = get_owned_event(db, event_id, organizer, lock=True)
    fields = data.model_dump(exclude_unset=True)

    rows: list[tuple[str, str]] = []
    description_changed = price_changed = False

    if "title" in fields and fields["title"] != event.title:
        rows += [("Previous title", event.title), ("New title", fields["title"])]
        event.title = fields["title"]
    if "venue" in fields and fields["venue"] != event.venue:
        rows += [("Previous venue", event.venue), ("New venue", fields["venue"])]
        event.venue = fields["venue"]
    if "event_date" in fields and fields["event_date"] != as_utc(event.event_date):
        rows += [("Previous date", fmt_dt(event.event_date)), ("New date", fmt_dt(fields["event_date"]))]
        event.event_date = fields["event_date"]
    if "ticket_price" in fields and Decimal(fields["ticket_price"]) != Decimal(event.ticket_price):
        rows += [("Previous price per ticket", fmt_money(event.ticket_price)),
                 ("New price per ticket", fmt_money(fields["ticket_price"]))]
        event.ticket_price = fields["ticket_price"]
        price_changed = True
    if "description" in fields and fields["description"] != event.description:
        event.description = fields["description"]
        description_changed = True
    if "capacity" in fields and fields["capacity"] != event.capacity:
        sold = event.capacity - event.available_tickets
        if fields["capacity"] < sold:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Capacity cannot be lower than the {sold} tickets already booked")
        event.capacity = fields["capacity"]
        event.available_tickets = fields["capacity"] - sold

    if rows or description_changed:
        # Only customers with a CONFIRMED booking for THIS event; one email per customer.
        customers = db.execute(
            select(User).join(Booking, Booking.customer_id == User.id)
            .where(Booking.event_id == event.id, Booking.status == "confirmed").distinct()
        ).scalars().all()
        update_id = uuid.uuid4().hex[:12]
        for c in customers:
            subject, text, html = event_updated(c.name, event.title, rows, description_changed, price_changed)
            enqueue_email(db, f"event-update:{event.id}:{update_id}:{c.id}", c.email, c.name, subject, text, html)

    db.commit()  # event change + queued notifications are one atomic unit
    return event


def delete_event(db: Session, event_id: int, organizer: User) -> None:
    event = get_owned_event(db, event_id, organizer, lock=True)
    confirmed = db.scalar(select(func.count(Booking.id)).where(Booking.event_id == event.id,
                                                               Booking.status == "confirmed"))
    if confirmed:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Event has {confirmed} confirmed booking(s) and cannot be deleted")
    db.execute(delete(Booking).where(Booking.event_id == event.id))  # only cancelled ones remain
    db.delete(event)
    db.commit()
