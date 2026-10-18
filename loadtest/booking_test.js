// k6 load test for the booking endpoint.
//
//   SCENARIO=oversell  -> VUS customers each fire ONE booking at the same moment for CAPACITY tickets (default 100 vs 5)
//   SCENARIO=ramp      -> ramp virtual users up in steps, every VU books 1 ticket in a loop (huge capacity),
//                         to find where latency climbs / errors start.
//
//   k6 run -e BASE_URL=https://YOUR-APP.onrender.com -e INVITE_CODE=xxx -e SCENARIO=oversell loadtest/booking_test.js
//   k6 run -e BASE_URL=... -e INVITE_CODE=xxx -e SCENARIO=ramp -e STAGES=10,25,50,100,150 loadtest/booking_test.js
//
// 201 = booked, 409 = sold out (EXPECTED, not a failure). Anything else is counted in `unexpected_errors`.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const BASE = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '') + '/api';
const INVITE = __ENV.INVITE_CODE || '';
const SCENARIO = __ENV.SCENARIO || 'oversell';
const VUS = parseInt(__ENV.VUS || '100');
const CAPACITY = parseInt(__ENV.CAPACITY || (SCENARIO === 'oversell' ? '5' : '10000000'));
const STAGES = (__ENV.STAGE_VUS || '10,25,50,100,150').split(',').map((x) => parseInt(x));
const STAGE_SECONDS = parseInt(__ENV.STAGE_SECONDS || '30');
const THINK = parseFloat(__ENV.THINK_SECONDS || '0');
const PASSWORD = 'LoadTest1234';
const MAX_VUS = SCENARIO === 'oversell' ? VUS : Math.max(...STAGES);

const booked = new Counter('bookings_confirmed');        // 201
const soldOut = new Counter('bookings_sold_out');        // 409
const unexpected = new Counter('unexpected_errors');     // everything else (5xx, 401, timeouts ...)
const bookingMs = new Trend('booking_duration_ms', true);

// Treat 409 as an expected response so k6's http_req_failed only counts real problems.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 409));

export const options = {
  setupTimeout: '300s',
  scenarios: {
    run:
      SCENARIO === 'oversell'
        ? { executor: 'per-vu-iterations', vus: VUS, iterations: 1, maxDuration: '2m' }
        : {
            executor: 'ramping-vus',
            startVUs: 0,
            gracefulRampDown: '10s',
            stages: STAGES.flatMap((v) => [
              { duration: '10s', target: v },
              { duration: `${STAGE_SECONDS}s`, target: v },
            ]),
          },
  },
  thresholds: {
    unexpected_errors: ['count==0'],
    'booking_duration_ms': ['p(95)<1500'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

export function setup() {
  const stamp = `${Date.now()}`;
  const orgEmail = `org-${stamp}@loadtest.dev`;
  let r = http.post(`${BASE}/auth/register`, JSON.stringify({
    name: 'Load Organizer', email: orgEmail, password: PASSWORD, role: 'organizer', invite_code: INVITE }),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'setup' } });
  if (r.status !== 201) throw new Error(`organizer register failed: ${r.status} ${r.body}`);
  r = http.post(`${BASE}/auth/login`, JSON.stringify({ email: orgEmail, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'setup' } });
  const orgToken = r.json('access_token');
  const auth = { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${orgToken}` }, tags: { name: 'setup' } };

  const eventDate = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString();
  r = http.post(`${BASE}/events`, JSON.stringify({
    title: `Load test ${stamp}`, description: 'k6', venue: 'Test Hall', event_date: eventDate,
    ticket_price: '100', capacity: CAPACITY }), auth);
  if (r.status !== 201) throw new Error(`event create failed: ${r.status} ${r.body}`);
  const eventId = r.json('id');

  // Pre-register every customer (in parallel) so the measured phase contains only booking traffic.
  const json = { headers: { 'Content-Type': 'application/json' }, tags: { name: 'setup' } };
  const emails = [];
  for (let i = 0; i < MAX_VUS; i++) emails.push(`cust-${stamp}-${i}@loadtest.dev`);
  const regs = http.batch(emails.map((e, i) => ['POST', `${BASE}/auth/register`,
    JSON.stringify({ name: `Cust ${i}`, email: e, password: PASSWORD }), json]));
  regs.forEach((x) => { if (x.status !== 201) throw new Error(`customer register failed: ${x.status} ${x.body}`); });
  const logins = http.batch(emails.map((e) => ['POST', `${BASE}/auth/login`,
    JSON.stringify({ email: e, password: PASSWORD }), json]));
  const tokens = logins.map((x) => {
    if (x.status !== 200) throw new Error(`customer login failed: ${x.status} ${x.body}`);
    return x.json('access_token');
  });
  console.log(`setup done: event ${eventId}, capacity ${CAPACITY}, ${tokens.length} customers`);
  return { eventId, orgToken, tokens };
}

export default function (data) {
  const token = data.tokens[(exec.vu.idInTest - 1) % data.tokens.length];
  const res = http.post(`${BASE}/events/${data.eventId}/bookings`, JSON.stringify({ quantity: 1 }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, tags: { name: 'book' } });
  bookingMs.add(res.timings.duration);
  if (res.status === 201) booked.add(1);
  else if (res.status === 409) soldOut.add(1);
  else unexpected.add(1);
  check(res, { 'status is 201 or 409': (r) => r.status === 201 || r.status === 409 });
  if (THINK > 0) sleep(THINK);
}

export function teardown(data) {
  // Let any in-flight requests settle, then compare confirmed tickets with the event's counters.
  sleep(2);
  const r = http.get(`${BASE}/organizer/events/${data.eventId}/bookings?limit=1`,
    { headers: { Authorization: `Bearer ${data.orgToken}` }, tags: { name: 'verify' } });
  const s = r.json('summary');
  console.log(`INVENTORY CHECK  event=${data.eventId} capacity=${s.capacity} available=${s.available_tickets} ` +
    `confirmed_tickets=${s.confirmed_tickets} confirmed_bookings=${s.confirmed_bookings} ` +
    `=> ${s.inventory_consistent && s.confirmed_tickets <= s.capacity ? 'PASS (no overbooking)' : 'FAIL (OVERBOOKED / INCONSISTENT)'}`);
}
