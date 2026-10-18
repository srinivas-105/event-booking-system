from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import require_role
from app.models import Booking, Event, User
from app.schemas import BookingSummary, EventOut, OrganizerEventBookings
from app.services.event_service import get_owned_event

router = APIRouter(prefix="/organizer", tags=["Organizer"])


@router.get("/events", response_model=list[EventOut])
def my_events(user: User = Depends(require_role("organizer")), db: Session = Depends(get_db)):
    """All events I own, with remaining tickets."""
    return db.scalars(select(Event).where(Event.organizer_id == user.id).order_by(Event.event_date)).all()


@router.get("/events/{event_id}/bookings", response_model=OrganizerEventBookings)
def event_bookings(event_id: int, limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
                   user: User = Depends(require_role("organizer")), db: Session = Depends(get_db)):
    event = get_owned_event(db, event_id, user)
    count, tickets, revenue = db.execute(
        select(func.count(Booking.id), func.coalesce(func.sum(Booking.quantity), 0),
               func.coalesce(func.sum(Booking.total_price), 0))
        .where(Booking.event_id == event_id, Booking.status == "confirmed")
    ).one()
    summary = BookingSummary(
        capacity=event.capacity, available_tickets=event.available_tickets,
        confirmed_bookings=count, confirmed_tickets=int(tickets), revenue=revenue,
        inventory_consistent=(int(tickets) == event.capacity - event.available_tickets and event.available_tickets >= 0),
    )
    bookings = db.scalars(
        select(Booking).where(Booking.event_id == event_id).options(joinedload(Booking.customer))
        .order_by(Booking.id).limit(limit).offset(offset)
    ).all()
    return OrganizerEventBookings(event=event, summary=summary, bookings=bookings)
