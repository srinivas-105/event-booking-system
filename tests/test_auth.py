from datetime import timedelta

from app.security import create_access_token
from tests.conftest import login, make_user, register


def test_register_login_me(client):
    user = register(client, "A@Example.com")
    assert user["email"] == "a@example.com" and user["role"] == "customer"
    assert "password" not in user and "password_hash" not in user
    headers = login(client, "a@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["id"] == user["id"]


def test_duplicate_email_rejected(client):
    register(client, "dup@example.com")
    r = client.post("/api/auth/register", json={"name": "x", "email": "DUP@example.com", "password": "Passw0rdX"})
    assert r.status_code == 409


def test_organizer_needs_invite_code(client):
    body = {"name": "Org", "email": "org@example.com", "password": "Passw0rdX", "role": "organizer"}
    assert client.post("/api/auth/register", json=body).status_code == 403
    assert client.post("/api/auth/register", json={**body, "invite_code": "wrong"}).status_code == 403
    assert client.post("/api/auth/register", json={**body, "invite_code": "test-invite"}).status_code == 201


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={"name": "x", "email": "w@example.com", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"name": "x", "email": "w@example.com", "password": "onlyletters"})
    assert r.status_code == 422


def test_wrong_password_and_unknown_user(client):
    register(client, "b@example.com")
    assert client.post("/api/auth/login", json={"email": "b@example.com", "password": "nope12345"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "who@example.com", "password": "nope12345"}).status_code == 401


def test_missing_invalid_and_expired_tokens(client):
    user, _ = make_user(client, "c@example.com")
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid token"
    expired = create_access_token(user["id"], "customer", expires_delta=timedelta(seconds=-5))
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and r.json()["detail"] == "Token has expired"
