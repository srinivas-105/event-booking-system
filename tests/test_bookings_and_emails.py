import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import EmailOutbox, Event
from app.services import email_worker
from tests.conftest import make_event, make_user


def outbox():
    with SessionLocal() as db:
        return db.scalars(select(EmailOutbox).order_by(EmailOutbox.id)).all()


def book(client, headers, event_id, qty=1):
    return client.post(f"/api/events/{event_id}/bookings", json={"quantity": qty}, headers=headers)


def test_booking_flow_and_confirmation_email(client):
    _, org = make_user(client, "o@example.com", "organizer")
    cust, ch = make_user(client, "Test.Customer@example.com", name="Test Customer")
    ev = make_event(client, org, capacity=10, ticket_price="200")

    r = book(client, ch, ev["id"], 3)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["reference"].startswith("BK-") and b["quantity"] == 3
    assert float(b["unit_price"]) == 200 and float(b["total_price"]) == 600
    assert client.get(f"/api/events/{ev['id']}").json()["available_tickets"] == 7

    rows = outbox()
    assert len(rows) == 1 and rows[0].status == "pending"
    assert rows[0].to_email == "test.customer@example.com"  # the registered address, not hardcoded
    assert rows[0].subject == "Booking Confirmed — College Hackathon 2026"
    for needle in ("Hi Test Customer", "Tickets booked: 3", "Price per ticket: ₹200",
                   "Total ticket price: ₹600", f"Booking reference: {b['reference']}"):
        assert needle in rows[0].text_body

    assert email_worker.process_batch() == 1
    assert outbox()[0].status == "sent" and outbox()[0].sent_at is not None
    assert email_worker.process_batch() == 0  # nothing left, nothing re-sent


def test_sold_out_validation_roles_404(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    _, c2 = make_user(client, "c2@example.com")
    ev = make_event(client, org, capacity=3)
    assert book(client, c1, ev["id"], 2).status_code == 201
    assert book(client, c2, ev["id"], 2).status_code == 409      # only 1 left
    assert book(client, c2, ev["id"], 1).status_code == 201
    assert book(client, c1, ev["id"], 1).status_code == 409      # sold out
    assert client.get(f"/api/events/{ev['id']}").json()["available_tickets"] == 0
    assert book(client, c1, ev["id"], 0).status_code == 422
    assert book(client, c1, ev["id"], 11).status_code == 422     # above MAX_TICKETS_PER_BOOKING
    assert book(client, c1, 9999, 1).status_code == 404
    assert book(client, org, ev["id"], 1).status_code == 403     # organizers cannot book
    assert client.post(f"/api/events/{ev['id']}/bookings", json={"quantity": 1}).status_code == 401
    assert len(outbox()) == 2                                    # rejected bookings queue no email


def test_my_bookings_are_private(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    _, c2 = make_user(client, "c2@example.com")
    ev = make_event(client, org)
    book(client, c1, ev["id"], 2)
    assert len(client.get("/api/bookings/my", headers=c1).json()) == 1
    assert client.get("/api/bookings/my", headers=c2).json() == []
    assert client.get("/api/bookings/my", headers=org).status_code == 403


def test_organizer_view_hides_secrets_and_enforces_ownership(client):
    _, org1 = make_user(client, "o1@example.com", "organizer")
    _, org2 = make_user(client, "o2@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org1, capacity=10)
    book(client, c1, ev["id"], 4)
    r = client.get(f"/api/organizer/events/{ev['id']}/bookings", headers=org1)
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["confirmed_tickets"] == 4 and data["summary"]["available_tickets"] == 6
    assert data["summary"]["inventory_consistent"] is True
    assert "password" not in r.text and "token" not in r.text.lower()
    assert client.get(f"/api/organizer/events/{ev['id']}/bookings", headers=org2).status_code == 403
    assert client.get(f"/api/organizer/events/{ev['id']}/bookings", headers=c1).status_code == 403
    assert [e["id"] for e in client.get("/api/organizer/events", headers=org1).json()] == [ev["id"]]


def test_cancel_restores_tickets(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    _, c2 = make_user(client, "c2@example.com")
    ev = make_event(client, org, capacity=5)
    b = book(client, c1, ev["id"], 3).json()
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=c2).status_code == 404
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=c1).json()["status"] == "cancelled"
    assert client.get(f"/api/events/{ev['id']}").json()["available_tickets"] == 5
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=c1).status_code == 409


def test_event_update_emails_only_booked_customers(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com", name="Cust One")
    _, c2 = make_user(client, "c2@example.com", name="Cust Two")
    make_user(client, "c3@example.com", name="Not Booked")
    _, c4 = make_user(client, "c4@example.com", name="Other Event")
    ev = make_event(client, org, venue="College Auditorium")
    other = make_event(client, org, title="Another Event")
    book(client, c1, ev["id"], 1)
    book(client, c2, ev["id"], 2)
    book(client, c4, other["id"], 1)
    email_worker.process_batch()
    before = len(outbox())

    r = client.put(f"/api/events/{ev['id']}", json={"venue": "Main Campus Hall"}, headers=org)
    assert r.status_code == 200 and r.json()["venue"] == "Main Campus Hall"
    new = outbox()[before:]
    assert sorted(m.to_email for m in new) == ["c1@example.com", "c2@example.com"]
    assert all(m.subject == "Important Update — College Hackathon 2026" for m in new)
    assert "Previous venue: College Auditorium" in new[0].text_body
    assert "New venue: Main Campus Hall" in new[0].text_body
    assert "Updated at:" in new[0].text_body and "Your booking remains valid" in new[0].text_body

    # booking untouched
    assert len(client.get("/api/bookings/my", headers=c1).json()) == 1
    # no-op update => no emails
    client.put(f"/api/events/{ev['id']}", json={"venue": "Main Campus Hall"}, headers=org)
    assert len(outbox()) == before + 2


def test_price_and_date_change_show_old_and_new(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org, ticket_price="200")
    book(client, c1, ev["id"], 1)
    before = len(outbox())
    client.put(f"/api/events/{ev['id']}", json={"ticket_price": "250", "event_date": "2030-01-01T10:00:00Z"}, headers=org)
    body = outbox()[before].text_body
    assert "Previous price per ticket: ₹200" in body and "New price per ticket: ₹250" in body
    assert "Previous date:" in body and "New date: 01 Jan 2030, 10:00 UTC" in body
    # the existing booking keeps its original price
    assert float(client.get("/api/bookings/my", headers=c1).json()[0]["unit_price"]) == 200


def test_capacity_cannot_drop_below_sold(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org, capacity=10)
    book(client, c1, ev["id"], 4)
    assert client.put(f"/api/events/{ev['id']}", json={"capacity": 3}, headers=org).status_code == 409
    r = client.put(f"/api/events/{ev['id']}", json={"capacity": 20}, headers=org)
    assert r.json()["capacity"] == 20 and r.json()["available_tickets"] == 16


def test_html_in_event_title_is_escaped_in_email(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org, title="<script>alert(1)</script> Fest")
    book(client, c1, ev["id"], 1)
    assert "<script>" not in outbox()[0].html_body


# ---------------------------------------------------------------- retry behaviour
def test_transient_failure_is_retried_with_same_idempotency_key(client, monkeypatch):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org)
    book(client, c1, ev["id"], 1)

    calls = []

    def flaky(to_email, to_name, subject, text, html, idempotency_key=None):
        calls.append(idempotency_key)
        if len(calls) == 1:
            raise email_worker.EmailSendError("provider returned 503")

    monkeypatch.setattr(email_worker, "send_email", flaky)
    email_worker.process_batch()
    row = outbox()[0]
    assert row.status == "pending" and row.attempts == 1 and "503" in row.last_error

    assert email_worker.process_batch() == 0  # backoff: not due yet
    with SessionLocal() as db:  # fast-forward the backoff
        r = db.get(EmailOutbox, row.id)
        from datetime import timedelta
        from app.utils import utcnow
        r.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    email_worker.process_batch()
    row = outbox()[0]
    assert row.status == "sent" and row.attempts == 2
    assert calls[0] == calls[1] and calls[0].startswith("booking-confirmation:")


def test_permanent_failure_is_not_retried(client, monkeypatch):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org)
    book(client, c1, ev["id"], 1)

    def bad(*a, **k):
        raise email_worker.EmailSendError("provider returned 403", permanent=True)

    monkeypatch.setattr(email_worker, "send_email", bad)
    email_worker.process_batch()
    assert outbox()[0].status == "failed"
    assert email_worker.process_batch() == 0


def test_booking_survives_email_outage(client, monkeypatch):
    """The booking is committed before any email is attempted, so a dead provider cannot lose it."""
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org)
    monkeypatch.setattr(email_worker, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(email_worker.EmailSendError("down")))
    assert book(client, c1, ev["id"], 2).status_code == 201
    email_worker.process_batch()
    assert len(client.get("/api/bookings/my", headers=c1).json()) == 1
    assert client.get(f"/api/events/{ev['id']}").json()["available_tickets"] == 8


def test_stale_lease_is_reclaimed(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, c1 = make_user(client, "c1@example.com")
    ev = make_event(client, org)
    book(client, c1, ev["id"], 1)
    from datetime import timedelta
    from app.utils import utcnow
    with SessionLocal() as db:  # simulate a worker that crashed mid-send
        r = db.scalars(select(EmailOutbox)).one()
        r.status = "processing"
        r.locked_until = utcnow() - timedelta(minutes=1)
        db.commit()
    assert email_worker.process_batch() == 1
    assert outbox()[0].status == "sent"
