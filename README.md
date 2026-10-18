Organizers invitecode: Ml8vEHSXK5sEqN8rOLEUNf9gdS-gkDNX

# Event Booking System (FastAPI + PostgreSQL)

A simplified BookMyShow-style backend. **Organizers** create and manage events, **customers** browse and book tickets,
and the system sends **real emails** (booking confirmation, event-update notification) from a background worker.
Tickets can never be oversold, even when many customers book at the same instant.

- **Live API:** `https://<YOUR-APP>.onrender.com`   |   **Swagger:** `https://<YOUR-APP>.onrender.com/docs`   |   **Health:** `/health`
- **Demo video:** `<link>`

## 1. Features
- Register / login with JWT; two roles (`organizer`, `customer`); organizer sign-up is **invite-only**.
- Events: create, browse (public, search + pagination), get, partial update, delete (only if no confirmed bookings), ownership checks.
- Bookings: atomic ticket reservation, unique booking reference (`BK-XXXXXXXXXXXX`), "my bookings", cancel (restores tickets).
- Organizer: list own events with remaining tickets, view bookings of an owned event with an inventory summary.
- Email: confirmation after a booking, update notice to customers with a confirmed booking for the changed event, via **Resend**.
- Reliability: transactional outbox, retries with exponential backoff, idempotency keys, lease-based crash recovery.
- Load testing: k6 script + a no-k6 Python script, with a switchable *baseline* vs *optimized* booking path.

## 2. Architecture and technology choices

```
Client (Swagger/Postman/k6)
   |  HTTPS + JWT
   v
FastAPI (routers -> services) ---- SQLAlchemy ----> PostgreSQL
   |                                                  users, events, bookings, email_outbox
   | (same DB transaction)                                   ^
   +--> INSERT booking + UPDATE tickets + INSERT outbox row  |
                                                             | claim rows (FOR UPDATE SKIP LOCKED)
                          Email worker thread  --------------+
                              |  POST https://api.resend.com/emails  (Idempotency-Key = dedupe_key)
                              v
                        Customer's inbox
```

| Choice | Why |
|---|---|
| **FastAPI** | Typed request validation (Pydantic), automatic Swagger at `/docs`, simple dependency injection for auth/roles. |
| **PostgreSQL** | Real row-level locking and concurrent writers; SQLite serialises all writes, so it cannot show realistic concurrency. SQLite is supported only for quick local runs / unit tests. |
| **SQLAlchemy 2.0** | One codebase for both databases; lets us write the atomic conditional `UPDATE` explicitly. |
| **bcrypt** | Slow, salted password hashing (cost configurable by `BCRYPT_ROUNDS`). |
| **PyJWT (HS256)** | Stateless auth. Role is re-checked from the database on every protected call, so a demoted/deleted user loses access immediately. |
| **Resend** | Simple transactional-email HTTP API with idempotency keys. |
| **Transactional outbox** | The email job is written in the same transaction as the booking, so it cannot be lost, and a rolled-back booking never emails. A broker (Redis) would be one more service to run; a DB table is enough here and easy to inspect. |
| **k6** | Scriptable concurrent load with p50/p95 and thresholds. |

Project layout:
```
app/main.py             app, lifespan (create tables, start worker), health endpoints
app/config.py           env-based settings (+ validation)
app/database.py         engine / pool / session (SQLite pragmas for tests)
app/models.py           users, events, bookings, email_outbox (+ CHECK constraints, indexes)
app/schemas.py          request/response models + validation
app/security.py         bcrypt + JWT
app/deps.py             get_current_user, require_role(...)
app/routers/            auth, events, bookings, organizer
app/services/           booking_service (atomic reservation), event_service (update + notify),
                        email_templates, email_service (Resend), outbox, email_worker
app/worker.py           optional stand-alone worker process
tests/                  25 tests (auth, events, bookings, emails, retries, concurrency)
loadtest/               booking_test.js (k6), oversell_check.py, RESULTS_TEMPLATE.md
docs/                   Postman collection, demo-video script
render.yaml             one-click Render Blueprint (web service + PostgreSQL)
```

## 3. Database

| Table | Columns (key ones) | Notes |
|---|---|---|
| `users` | id, name, email (unique), password_hash, role, created_at | `role IN ('organizer','customer')` |
| `events` | id, title, description, venue, event_date, ticket_price, capacity, available_tickets, organizer_id -> users, created_at | CHECK: `capacity > 0`, `0 <= available_tickets <= capacity`, `ticket_price >= 0` |
| `bookings` | id, reference (unique), customer_id -> users, event_id -> events, quantity, unit_price, total_price, status, created_at | `unit_price` is a snapshot, so later price changes do not alter existing bookings. Index on `(event_id, status)`. |
| `email_outbox` | id, dedupe_key (unique), to_email, subject, text_body, html_body, status, attempts, next_attempt_at, locked_until, last_error, sent_at | Statuses: `pending`, `processing`, `sent`, `failed`. Index on `(status, next_attempt_at)`. |

Relations: a user (organizer) has many events; a user (customer) has many bookings; an event has many bookings.
Tables are created at startup with `create_all` (no migration tool; for a schema change in production, add Alembic).

## 4. Authentication and authorization
- `POST /api/auth/register` -> `role` defaults to `customer`. `role=organizer` requires `invite_code` equal to the `ORGANIZER_INVITE_CODE` env var (constant-time compare). If the variable is empty, nobody can become an organizer.
- `POST /api/auth/login` -> JWT (`sub`=user id, `exp`). Expired token -> `401 Token has expired`; bad token -> `401 Invalid token`.
- `require_role("organizer" | "customer")` guards every protected route; wrong role -> `403`.
- Organizers can only update/delete/view **their own** events (`403` otherwise). Customers only see **their own** bookings (the query is filtered by the token's user, so there is no id to tamper with); cancelling someone else's booking returns `404`.
- Organizer booking view returns only `id, name, email` of each customer, never password hashes or tokens.
- Secrets (`SECRET_KEY`, `RESEND_API_KEY`, DB URL) are environment variables; 500 errors return a generic message and log details server-side only. Outside `ENVIRONMENT=development` the app refuses to start with a weak `SECRET_KEY`.

## 5. API (prefix `/api`)

| Method | Path | Access |
|---|---|---|
| POST | `/auth/register` | public |
| POST | `/auth/login` | public |
| GET | `/auth/me` | any logged-in user |
| POST | `/events` | organizer |
| GET | `/events` (`q`, `include_past`, `limit`, `offset`) | public |
| GET | `/events/{id}` | public |
| PUT | `/events/{id}` (partial) | owning organizer |
| DELETE | `/events/{id}` | owning organizer (409 if confirmed bookings exist) |
| POST | `/events/{id}/bookings` | customer |
| GET | `/bookings/my` | customer |
| POST | `/bookings/{id}/cancel` | customer (owner) |
| GET | `/organizer/events` | organizer |
| GET | `/organizer/events/{id}/bookings` | owning organizer |
| GET | `/health`, `/health/ready` | public (not under `/api`) |

Examples:
```bash
BASE=http://localhost:8000
curl -X POST $BASE/api/auth/register -H 'Content-Type: application/json' \
  -d '{"name":"Test Customer","email":"cust@example.com","password":"Passw0rd123"}'

curl -X POST $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"cust@example.com","password":"Passw0rd123"}'        # -> {"access_token":"...","token_type":"bearer","expires_in":3600}

curl -X POST $BASE/api/events -H "Authorization: Bearer $ORG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"College Hackathon 2026","description":"24h","venue":"College Auditorium",
       "event_date":"2030-03-15T09:00:00Z","ticket_price":"200","capacity":50}'

curl -X POST $BASE/api/events/1/bookings -H "Authorization: Bearer $CUST_TOKEN" -H 'Content-Type: application/json' \
  -d '{"quantity":3}'        # 201 booked | 409 not enough tickets | 404 no such event

curl -X PUT $BASE/api/events/1 -H "Authorization: Bearer $ORG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"venue":"Main Campus Hall"}'      # emails every customer with a confirmed booking for event 1
```
A Postman collection is in `docs/postman_collection.json` (it stores tokens and the event id automatically).

## 6. Preventing overbooking
The reservation is one atomic statement inside the booking transaction:

```sql
UPDATE events
SET available_tickets = available_tickets - :qty
WHERE id = :id AND available_tickets >= :qty AND event_date > now();
-- rowcount = 1 -> reserved, then INSERT booking + INSERT outbox row, COMMIT
-- rowcount = 0 -> event missing (404), already past (409) or sold out (409)
```
PostgreSQL locks the row for the `UPDATE`; concurrent requests wait and then re-check `available_tickets >= :qty`
against the latest committed value, so two requests can never both take the last ticket. The booking row, the ticket
decrement and the queued email commit together or not at all. A `CHECK (available_tickets >= 0)` constraint is a second safety net.

The *naive* version (`BOOKING_MODE=baseline`: read the event, check in Python, write back `available - qty`) has a lost-update race and is kept
**only** to produce the "before" measurements. Event updates take `SELECT ... FOR UPDATE` on the same row, so a capacity change cannot interleave with bookings.

**Connection starvation (found while load testing).** FastAPI runs each sync dependency/endpoint in a worker thread. When more requests were
admitted than there were DB connections, every worker thread ended up blocked waiting for a connection that was held by a request which needed a free
thread to continue, and requests stalled until the pool timeout (30 s) and then failed with 500. Two fixes are in the code: sessions are closed on a
separate small executor (`app/database.py`), and an ASGI `ConcurrencyLimiter` (`app/main.py`) admits at most `DB_POOL_SIZE + DB_MAX_OVERFLOW - 2`
requests at a time (override with `MAX_CONCURRENT_REQUESTS`); the rest wait cheaply in the event loop. If you raise concurrency, raise the pool size
together with it, within your database plan's connection limit.

Automated proof: `tests/test_concurrency.py` (100 threads race for 5 tickets: exactly 5 succeed, 95 are rejected, stock ends at 0) and
`loadtest/oversell_check.py` / `booking_test.js` against a running server.

## 7. Background email architecture and retry behaviour
1. Booking or event update **commits** and, in the same transaction, inserts `email_outbox` rows (`pending`).
2. An in-process worker thread (started in the app lifespan, `RUN_EMAIL_WORKER=true`) polls every `EMAIL_POLL_SECONDS`, claims due rows with
   `FOR UPDATE SKIP LOCKED` and sets `processing` with a 2-minute lease. Several app instances or `python -m app.worker` processes can run safely together.
3. It calls Resend with `Idempotency-Key = dedupe_key` (e.g. `booking-confirmation:BK-...`, `event-update:<event>:<update>:<customer>`).
4. Success -> `sent`. Temporary failure (network error, timeout, 429, 5xx) -> `pending` with backoff 10s, 20s, 40s ... (max 10 min), up to `EMAIL_MAX_ATTEMPTS` (5), then `failed`.
   Permanent failure (other 4xx such as an unverified domain) -> `failed` immediately, with `last_error` recorded (never the API key).
5. If the process dies mid-send, the lease expires and the row is retried; the idempotency key prevents a duplicate email (Resend keeps keys for a limited time).
6. A slow or dead email provider **never** affects the booking: the booking is already committed.

Inspect / re-queue failures:
```sql
SELECT id, to_email, status, attempts, last_error FROM email_outbox ORDER BY id DESC LIMIT 20;
UPDATE email_outbox SET status='pending', attempts=0, next_attempt_at=now() WHERE status='failed';
```
Limitations: the worker is at-least-once with provider-side de-duplication; there is no dead-letter alerting; on a free Render web service the worker only runs while the service is awake.

**Resend setup:** create an account, create an API key, put it in `RESEND_API_KEY`. Without a verified domain Resend only lets you send from
`onboarding@resend.dev` **to the email address of your own Resend account**, so register your demo customer with that address. To email anyone,
verify a domain in Resend and set `EMAIL_FROM` to an address on it.

## 8. Local setup
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                     # then edit: SECRET_KEY, ORGANIZER_INVITE_CODE, RESEND_API_KEY, EMAIL_FROM
docker compose up -d db                                  # local PostgreSQL (or set DATABASE_URL=sqlite:///./dev.db)
uvicorn app.main:app --reload                            # http://localhost:8000/docs
pytest -q                                                # 25 tests on SQLite
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/event_booking pytest -q   # same tests on PostgreSQL
```

Environment variables (see `.env.example` for the full list): `DATABASE_URL`, `SECRET_KEY`, `ORGANIZER_INVITE_CODE`, `RESEND_API_KEY`, `EMAIL_FROM`,
`EMAIL_PROVIDER` (`resend` | `mock`), `BOOKING_MODE` (`optimized` | `baseline`), `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `MAX_CONCURRENT_REQUESTS`, `THREADPOOL_SIZE`, `BCRYPT_ROUNDS`, `RUN_EMAIL_WORKER`.

## 9. Deployment (Render)
1. Push this repo to GitHub.
2. Render dashboard -> **New + -> Blueprint** -> select the repo. `render.yaml` creates the PostgreSQL database and the web service (`DATABASE_URL` and a random `SECRET_KEY` are wired automatically).
3. In the web service's **Environment** tab set `RESEND_API_KEY`, `EMAIL_FROM`, `ORGANIZER_INVITE_CODE`.
4. After the deploy, open `https://<YOUR-APP>.onrender.com/docs` and `/health/ready`.
5. Test the **deployed** URL (Swagger/Postman), not localhost. Check Render's current free-tier limits (services sleep when idle, free databases are time-limited); use a paid plan for the final load tests if the free CPU is the limit.

## 10. Load testing
Install [k6](https://k6.io/docs/get-started/installation/). Everything below targets the deployed URL.

```bash
# Oversell test: 100 customers x 1 ticket each, 5 tickets, all at once
k6 run -e BASE_URL=https://<APP>.onrender.com -e INVITE_CODE=<code> -e SCENARIO=oversell loadtest/booking_test.js
# or without k6:
python loadtest/oversell_check.py --base-url https://<APP>.onrender.com --invite-code <code>

# Ramp test: step up virtual users, every VU books 1 ticket repeatedly (capacity 10,000,000)
k6 run -e BASE_URL=https://<APP>.onrender.com -e INVITE_CODE=<code> -e SCENARIO=ramp \
       -e STAGE_VUS=10,25,50,100,150 -e STAGE_SECONDS=30 loadtest/booking_test.js
```
Reading the output: `bookings_confirmed` (201) and `bookings_sold_out` (409, **expected**) are fine; `unexpected_errors` (5xx, timeouts, 401) must be 0.
`http_req_duration{name:book}` gives median and p95; the `INVENTORY CHECK` line printed at the end compares confirmed tickets with the event counters.

**Methodology**
1. Deploy with `BOOKING_MODE=baseline`. Run the oversell test, then the ramp test; note the VU level where p95 climbs sharply or errors start (the *breaking point*), and any oversold inventory.
2. Find the bottleneck (Render metrics for CPU/memory, `X-Process-Time-Ms` response header, `/health/ready` queue depth, database connections).
3. Switch to `BOOKING_MODE=optimized` (atomic update + outbox), tune `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` (the request limiter follows them), and repeat the same tests. Change one thing at a time.
4. Record both runs in `loadtest/RESULTS_TEMPLATE.md` and copy the table below.

What differs between the two modes: *baseline* = read/check/write in the app plus a **blocking email call inside the request**;
*optimized* = single atomic conditional `UPDATE`, shorter transaction, email moved to the outbox worker.
For load tests use `EMAIL_PROVIDER=mock` (a real provider would spam an inbox and hit rate limits). To make the baseline comparison honest, set
`EMAIL_MOCK_LATENCY_MS` to a latency you measured from a real Resend call and state this in your write-up. The demo of *real* emails is done separately with `EMAIL_PROVIDER=resend`.

### Results (fill with your own measurements; do not invent numbers)
| | Baseline | Optimized |
|---|---|---|
| Oversell test: confirmed / sold-out / errors | | |
| Inventory after test (available, confirmed tickets) | | |
| Breaking point (VUs) | | |
| p50 / p95 at that load (ms) | | |
| Max stable VUs / req/s | | |
| Bottleneck identified | | |

### Limitations
- Results depend on the hosting plan (shared CPU, small DB); they describe this deployment, not "the" system.
- All bookings of one event serialise on one row lock, which is what guarantees correctness; a very hot single event is therefore bounded by single-row update throughput.
- No payment step: "price" is informational; the system does not charge cards.
- No rate limiting, email verification or password reset.
