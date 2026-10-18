# Load-test results (fill in with YOUR real measurements)

Never invent numbers. Copy them from the k6 summary and the `INVENTORY CHECK` line.

**Environment**: Render plan: ____  | DB plan: ____ | region: ____ | date: ____
**Settings**: BOOKING_MODE=____  EMAIL_PROVIDER=mock  EMAIL_MOCK_LATENCY_MS=____  DB_POOL_SIZE=____  DB_MAX_OVERFLOW=____  THREADPOOL_SIZE=____  uvicorn workers=____

## A. Oversell test (100 customers, 5 tickets, one request each, simultaneous)

| | Baseline (`BOOKING_MODE=baseline`) | Optimized (`BOOKING_MODE=optimized`) |
|---|---|---|
| 201 confirmed | | |
| 409 sold out | | |
| unexpected errors (5xx/timeouts) | | |
| `available_tickets` at the end | | |
| confirmed tickets in DB | | |
| inventory check | | |
| median / p95 latency (ms) | | |

## B. Ramp test (large capacity, VUs stepped up, 1 ticket per request)

| VUs | Baseline req/s | Baseline p50 / p95 (ms) | Baseline errors | Optimized req/s | Optimized p50 / p95 (ms) | Optimized errors |
|---|---|---|---|---|---|---|
| 10 | | | | | | |
| 25 | | | | | | |
| 50 | | | | | | |
| 100 | | | | | | |
| 150 | | | | | | |

**Baseline breaking point** (first VU level where p95 jumps or errors appear): ____
**Optimized limit on the same hardware**: ____
**Bottleneck found** (CPU? DB pool wait? inline email? row lock?): ____
**What I changed and the measured effect**: ____
