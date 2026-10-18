# Demo video script (target ~4 minutes, face visible, English)

Record with your camera on (OBS, Zoom or Loom "screen + camera"). Do a dry run first. Use the DEPLOYED URL everywhere.

**Prep (before recording)**
- Register the customer with an inbox you can open on screen (with Resend's free test sender this must be the email of your Resend account).
- Open: Swagger (`<url>/docs`), your inbox, a terminal with k6, the README results table.
- Have two results ready from real runs: baseline (`BOOKING_MODE=baseline`) and optimized.

| Time | Show | Say (in your own words) |
|---|---|---|
| 0:00-0:30 | Your face + README title | Name, project, two roles (organizer / customer), stack: FastAPI, PostgreSQL, Resend, k6, Render. |
| 0:30-1:10 | Swagger on the deployed URL | Register organizer (invite code), login, **Authorize**, create event, register/login customer, book 3 tickets. Point out the 201 and the booking reference. |
| 1:10-1:50 | Inbox | Show the real confirmation email. Then update the venue as organizer; show the update email (previous venue / new venue). Mention only booked customers get it. |
| 1:50-2:40 | k6 baseline run | Run `SCENARIO=oversell` against baseline: show confirmed > capacity (oversold). Then the ramp run: where latency/errors start (your measured breaking point). |
| 2:40-3:30 | Optimizations + k6 optimized run | Explain: atomic conditional UPDATE, outbox + background worker (email off the request path), pool tuning. Show the optimized numbers and `PASS (no overbooking)`. |
| 3:30-4:00 | Summary + URL | Before/after numbers, limitations, show the live `/docs` and `/health` URL. |

Tip: keep the oversell demo to 100 customers x 5 tickets; it is easy to follow on screen.
