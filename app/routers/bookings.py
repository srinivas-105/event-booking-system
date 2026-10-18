from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.deps import require_role
from app.models import Booking, User
from app.schemas import BookingCreate, BookingOut
from app.services import booking_service as svc

router = APIRouter(tags=["Bookings"])
settings = get_settings()


@router.post("/events/{event_id}/bookings", response_model=BookingOut, status_code=201)
def book_tickets(event_id: int, payload: BookingCreate, user: User = Depends(require_role("customer")),
                 db: Session = Depends(get_db)):
    create = svc.create_booking_baseline if settings.booking_mode == "baseline" else svc.create_booking
    try:
        return create(db, user, event_id, payload.quantity)
    except svc.EventNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    except svc.EventEnded:
        raise HTTPException(status.HTTP_409_CONFLICT, "This event has already taken place")
    except svc.SoldOut:
        raise HTTPException(status.HTTP_409_CONFLICT, "Not enough tickets available")


@router.get("/bookings/my", response_model=list[BookingOut])
def my_bookings(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                user: User = Depends(require_role("customer")), db: Session = Depends(get_db)):
    stmt = (select(Booking).where(Booking.customer_id == user.id).options(joinedload(Booking.event))
            .order_by(Booking.id.desc()).limit(limit).offset(offset))
    return db.scalars(stmt).all()


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: int, user: User = Depends(require_role("customer")), db: Session = Depends(get_db)):
    try:
        booking = svc.cancel_booking(db, user, booking_id)
    except svc.BookingNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    except svc.BookingNotCancellable:
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking cannot be cancelled")
    return booking
