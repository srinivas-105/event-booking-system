"""Regenerates docs/postman_collection.json  (python docs/make_postman.py)"""
import json

def req(name, method, path, body=None, auth=None, test=None):
    r = {"name": name, "request": {"method": method, "header": [{"key": "Content-Type", "value": "application/json"}],
         "url": {"raw": "{{baseUrl}}/api" + path, "host": ["{{baseUrl}}"], "path": ["api"] + [p for p in path.split("/") if p]}}}
    if auth:
        r["request"]["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": "{{" + auth + "}}", "type": "string"}]}
    if body is not None:
        r["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2)}
    if test:
        r["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": test}}]
    return r

save = lambda var: [f"pm.collectionVariables.set('{var}', pm.response.json().access_token);"]
items = [
    req("1. Register organizer (invite code)", "POST", "/auth/register",
        {"name": "Olivia Organizer", "email": "organizer@example.com", "password": "Passw0rd123", "role": "organizer", "invite_code": "{{inviteCode}}"}),
    req("2. Login organizer", "POST", "/auth/login", {"email": "organizer@example.com", "password": "Passw0rd123"}, test=save("organizerToken")),
    req("3. Register customer", "POST", "/auth/register",
        {"name": "Test Customer", "email": "{{customerEmail}}", "password": "Passw0rd123"}),
    req("4. Login customer", "POST", "/auth/login", {"email": "{{customerEmail}}", "password": "Passw0rd123"}, test=save("customerToken")),
    req("5. Create event (organizer)", "POST", "/events",
        {"title": "College Hackathon 2026", "description": "24-hour hackathon", "venue": "College Auditorium",
         "event_date": "2030-03-15T09:00:00Z", "ticket_price": "200", "capacity": 50}, auth="organizerToken",
        test=["pm.collectionVariables.set('eventId', pm.response.json().id);"]),
    req("6. Browse events (public)", "GET", "/events"),
    req("7. Get event (public)", "GET", "/events/{{eventId}}"),
    req("8. Book 3 tickets (customer) -> confirmation email", "POST", "/events/{{eventId}}/bookings", {"quantity": 3}, auth="customerToken"),
    req("9. My bookings (customer)", "GET", "/bookings/my", auth="customerToken"),
    req("10. Update venue (organizer) -> update email to booked customers", "PUT", "/events/{{eventId}}",
        {"venue": "Main Campus Hall"}, auth="organizerToken"),
    req("11. Organizer: bookings + remaining tickets", "GET", "/organizer/events/{{eventId}}/bookings", auth="organizerToken"),
    req("12. Auth: who am I", "GET", "/auth/me", auth="customerToken"),
]
col = {"info": {"name": "Event Booking System", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
       "variable": [{"key": "baseUrl", "value": "http://localhost:8000"}, {"key": "inviteCode", "value": "your-invite-code"},
                    {"key": "customerEmail", "value": "YOUR_REAL_EMAIL@example.com"}],
       "item": items}
json.dump(col, open("docs/postman_collection.json", "w"), indent=2)
print("written docs/postman_collection.json")
