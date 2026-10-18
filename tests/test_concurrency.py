"""Overbooking test: many threads, each with its own DB session, race for the last tickets."""
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Booking, Event, User
from app.security import hash_password
from app.services import booking_service as svc
from tests.conftest import make_event, make_user


def test_100_customers_race_for_5_tickets(client):
    _, org = make_user(client, "o@example.com", "organizer")
    ev = make_event(client, org, capacity=5)
    with SessionLocal() as db:
        users = [User(name=f"u{i}", email=f"u{i}@example.com", password_hash=hash_password("Passw0rdX"),
                      role="customer") for i in range(100)]
        db.add_all(users)
        db.commit()
        db.expunge_all()  # detached copies: threads only read id/name/email from them

    barrier = threading.Barrier(100)

    def attempt(customer):
        barrier.wait()  # everyone fires at the same moment (no DB connection is held while waiting)
        with SessionLocal() as db:
            try:
                svc.create_booking(db, customer, ev["id"], 1)
                return "ok"
            except svc.SoldOut:
                return "sold_out"

    with ThreadPoolExecutor(max_workers=100) as pool:
        results = list(pool.map(attempt, users))

    assert results.count("ok") == 5
    assert results.count("sold_out") == 95
    with SessionLocal() as db:
        assert db.get(Event, ev["id"]).available_tickets == 0
        confirmed = db.scalar(select(func.coalesce(func.sum(Booking.quantity), 0))
                              .where(Booking.event_id == ev["id"], Booking.status == "confirmed"))
        assert confirmed == 5
