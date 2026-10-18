from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.models import Event, User
from app.schemas import EventCreate, EventOut, EventUpdate
from app.services import event_service
from app.utils import utcnow

router = APIRouter(prefix="/events", tags=["Events"])


@router.post("", response_model=EventOut, status_code=201)
def create_event(payload: EventCreate, user: User = Depends(require_role("organizer")),
                 db: Session = Depends(get_db)):
    event = Event(**payload.model_dump(), available_tickets=payload.capacity, organizer_id=user.id)
    db.add(event)
    db.commit()
    return event


@router.get("", response_model=list[EventOut])
def list_events(
    q: str | None = Query(default=None, max_length=100, description="Search title or venue"),
    include_past: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Event).order_by(Event.event_date.asc(), Event.id.asc())
    if not include_past:
        stmt = stmt.where(Event.event_date > utcnow())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Event.title.ilike(like), Event.venue.ilike(like)))
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


@router.put("/{event_id}", response_model=EventOut)
def update_event(event_id: int, payload: EventUpdate, user: User = Depends(require_role("organizer")),
                 db: Session = Depends(get_db)):
    """Partial update. Changing title/venue/date/price/description queues an email to every customer
    with a confirmed booking for this event (and nobody else)."""
    return event_service.update_event(db, event_id, user, payload)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, user: User = Depends(require_role("organizer")), db: Session = Depends(get_db)):
    event_service.delete_event(db, event_id, user)
    return Response(status_code=204)
