import os
import tempfile

# Must be set BEFORE the app is imported (settings are read at import time).
_tmp = tempfile.mkdtemp()
os.environ.update({
    # Set TEST_DATABASE_URL=postgresql://... to run the whole suite against PostgreSQL instead of SQLite.
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db",
    "EMAIL_PROVIDER": "mock",
    "RUN_EMAIL_WORKER": "false",
    "BCRYPT_ROUNDS": "4",
    "ORGANIZER_INVITE_CODE": "test-invite",
    "SECRET_KEY": "test-secret-key-test-secret-key-1234",
    "BOOKING_MODE": "optimized",
})

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def future(days=30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def register(client, email, role="customer", name="Test User", password="Passw0rdX"):
    body = {"name": name, "email": email, "password": password, "role": role}
    if role == "organizer":
        body["invite_code"] = "test-invite"
    r = client.post("/api/auth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def login(client, email, password="Passw0rdX") -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_user(client, email, role="customer", name="Test User"):
    user = register(client, email, role, name)
    return user, login(client, email)


def make_event(client, org_headers, **over):
    body = {"title": "College Hackathon 2026", "description": "24h hackathon", "venue": "College Auditorium",
            "event_date": future(), "ticket_price": "200", "capacity": 10}
    body.update(over)
    r = client.post("/api/events", json=body, headers=org_headers)
    assert r.status_code == 201, r.text
    return r.json()
