#!/usr/bin/env python3
"""Overbooking proof without k6: N customers fire ONE booking each at the same instant for `capacity` tickets.

    python loadtest/oversell_check.py --base-url http://localhost:8000 --invite-code <ORGANIZER_INVITE_CODE>

Prints counts, latencies and the inventory check, and exits 1 if the inventory is inconsistent.
Expected on a correct server: capacity x 201, (customers - capacity) x 409, available_tickets == 0.
"""
import argparse
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

p = argparse.ArgumentParser()
p.add_argument("--base-url", default="http://localhost:8000")
p.add_argument("--invite-code", required=True)
p.add_argument("--customers", type=int, default=100)
p.add_argument("--capacity", type=int, default=5)
args = p.parse_args()

base = args.base_url.rstrip("/") + "/api"
run = uuid.uuid4().hex[:8]
PASSWORD = "LoadTest1234"
TIMEOUT = httpx.Timeout(60.0)


def must(resp, code):
    if resp.status_code != code:
        sys.exit(f"unexpected {resp.status_code} from {resp.request.url}: {resp.text[:300]}")
    return resp.json()


with httpx.Client(timeout=TIMEOUT) as c:
    must(c.post(f"{base}/auth/register", json={"name": "Load Org", "email": f"org-{run}@loadtest.dev",
                                               "password": PASSWORD, "role": "organizer",
                                               "invite_code": args.invite_code}), 201)
    org = {"Authorization": "Bearer " + must(c.post(f"{base}/auth/login", json={
        "email": f"org-{run}@loadtest.dev", "password": PASSWORD}), 200)["access_token"]}
    event = must(c.post(f"{base}/events", headers=org, json={
        "title": f"Oversell check {run}", "description": "load test", "venue": "Test Hall",
        "event_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "ticket_price": "100", "capacity": args.capacity}), 201)

print(f"event {event['id']} created with capacity {args.capacity}; registering {args.customers} customers ...")


def make_customer(i):
    with httpx.Client(timeout=TIMEOUT) as c:
        email = f"cust-{run}-{i}@loadtest.dev"
        must(c.post(f"{base}/auth/register", json={"name": f"Cust {i}", "email": email, "password": PASSWORD}), 201)
        return must(c.post(f"{base}/auth/login", json={"email": email, "password": PASSWORD}), 200)["access_token"]


with ThreadPoolExecutor(max_workers=20) as pool:
    tokens = list(pool.map(make_customer, range(args.customers)))

barrier = threading.Barrier(args.customers)
results = []


def fire(token):
    with httpx.Client(timeout=TIMEOUT) as c:
        barrier.wait()
        t0 = time.perf_counter()
        try:
            r = c.post(f"{base}/events/{event['id']}/bookings", json={"quantity": 1},
                       headers={"Authorization": f"Bearer {token}"})
            return r.status_code, (time.perf_counter() - t0) * 1000
        except httpx.HTTPError as exc:
            return type(exc).__name__, (time.perf_counter() - t0) * 1000


print("firing ...")
with ThreadPoolExecutor(max_workers=args.customers) as pool:
    results = list(pool.map(fire, tokens))

codes = {}
for code, _ in results:
    codes[code] = codes.get(code, 0) + 1
lat = sorted(ms for _, ms in results)
p95 = lat[int(0.95 * (len(lat) - 1))]

with httpx.Client(timeout=TIMEOUT) as c:
    data = must(c.get(f"{base}/organizer/events/{event['id']}/bookings?limit=1", headers=org), 200)
s = data["summary"]

print("\n=== RESULT ===")
print(f"responses           : {codes}")
print(f"latency ms          : median={statistics.median(lat):.0f}  p95={p95:.0f}  max={lat[-1]:.0f}")
print(f"capacity            : {s['capacity']}")
print(f"available_tickets   : {s['available_tickets']}")
print(f"confirmed tickets   : {s['confirmed_tickets']}  (confirmed bookings: {s['confirmed_bookings']})")
ok = (s["inventory_consistent"] and s["confirmed_tickets"] <= s["capacity"]
      and codes.get(201, 0) == min(args.customers, args.capacity)
      and all(k in (201, 409) for k in codes))
print("VERDICT             :", "PASS - no overbooking" if ok else "FAIL - oversold or unexpected errors")
sys.exit(0 if ok else 1)
