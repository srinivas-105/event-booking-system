from tests.conftest import future, make_event, make_user


def test_only_organizer_can_create(client):
    _, cust = make_user(client, "c@example.com")
    r = client.post("/api/events", json={"title": "Some Event", "venue": "Hall", "event_date": future(),
                                         "ticket_price": "10", "capacity": 5}, headers=cust)
    assert r.status_code == 403
    assert client.post("/api/events", json={}).status_code == 401


def test_create_browse_get(client):
    _, org = make_user(client, "o@example.com", "organizer")
    ev = make_event(client, org, capacity=7)
    assert ev["available_tickets"] == 7
    listing = client.get("/api/events").json()  # public, no token
    assert [e["id"] for e in listing] == [ev["id"]]
    assert client.get(f"/api/events/{ev['id']}").json()["title"] == "College Hackathon 2026"
    assert client.get("/api/events/9999").status_code == 404
    assert client.get("/api/events?q=hackathon").json()[0]["id"] == ev["id"]
    assert client.get("/api/events?q=zzzz").json() == []


def test_validation(client):
    _, org = make_user(client, "o@example.com", "organizer")
    base = {"title": "Valid Title", "venue": "Hall", "event_date": future(), "ticket_price": "10", "capacity": 5}
    for bad in ({"capacity": 0}, {"ticket_price": "-1"}, {"event_date": "2020-01-01T00:00:00Z"},
                {"title": "x"}, {"ticket_price": "10.555"}):
        r = client.post("/api/events", json={**base, **bad}, headers=org)
        assert r.status_code == 422, (bad, r.text)


def test_ownership_on_update_and_delete(client):
    _, org1 = make_user(client, "o1@example.com", "organizer")
    _, org2 = make_user(client, "o2@example.com", "organizer")
    ev = make_event(client, org1)
    assert client.put(f"/api/events/{ev['id']}", json={"venue": "Elsewhere"}, headers=org2).status_code == 403
    assert client.delete(f"/api/events/{ev['id']}", headers=org2).status_code == 403
    assert client.put(f"/api/events/{ev['id']}", json={"venue": "Elsewhere"}, headers=org1).status_code == 200
    assert client.put(f"/api/events/{ev['id']}", json={}, headers=org1).status_code == 422
    assert client.put(f"/api/events/{ev['id']}", json={"venue": None}, headers=org1).status_code == 422
    assert client.delete(f"/api/events/{ev['id']}", headers=org1).status_code == 204
    assert client.get(f"/api/events/{ev['id']}").status_code == 404


def test_cannot_delete_event_with_confirmed_bookings(client):
    _, org = make_user(client, "o@example.com", "organizer")
    _, cust = make_user(client, "c@example.com")
    ev = make_event(client, org)
    assert client.post(f"/api/events/{ev['id']}/bookings", json={"quantity": 1}, headers=cust).status_code == 201
    assert client.delete(f"/api/events/{ev['id']}", headers=org).status_code == 409
