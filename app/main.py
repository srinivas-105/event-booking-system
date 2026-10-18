import asyncio
import logging
import time
from sqlalchemy import select
from app.database import SessionLocal
from app.models import EmailOutbox
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from app import models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import EmailOutbox
from app.routers import auth, bookings, events, organizer
from app.services.email_worker import EmailWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")
settings = get_settings()


class ConcurrencyLimiter:
    """Admit at most `limit` requests at a time (pure ASGI, no per-request overhead beyond a semaphore).

    Why it exists: FastAPI runs every sync dependency/endpoint in a worker thread (one hop each). If more
    requests are admitted than there are DB connections, all worker threads can end up blocked waiting for a
    connection that is held by a request which needs a free thread to continue -> stall until the pool times out.
    Keeping admitted requests <= pool capacity guarantees every admitted request can get its connection.
    Health/docs endpoints bypass the limiter so probes still answer under load."""

    def __init__(self, app, limit: int):
        self.app = app
        self.sem = asyncio.Semaphore(limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in ("/health", "/health/ready", "/docs", "/openapi.json", "/"):
            return await self.app(scope, receive, send)
        async with self.sem:
            await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.threadpool_size
    worker = None
    if settings.run_email_worker:
        worker = EmailWorker()
        worker.start()
    logger.info("started: booking_mode=%s email_provider=%s request_limit=%s",
                settings.booking_mode, settings.email_provider, settings.request_limit)
    yield
    if worker:
        worker.stop()


app = FastAPI(
    title="Event Booking System",
    version="1.0.0",
    description="Organizers create events, customers book tickets, emails are sent in the background.\n\n"
                "Click **Authorize** and paste the `access_token` from `/api/auth/login`.",
    lifespan=lifespan,
)


app.add_middleware(ConcurrencyLimiter, limit=settings.request_limit)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return response


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    logger.exception("unhandled error")  # details go to logs only, never to the client
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


for r in (auth.router, events.router, bookings.router, organizer.router):
    app.include_router(r, prefix="/api")


@app.get("/health", tags=["Health"])
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
def ready():
    """Readiness: database reachable; also shows email queue depth."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            rows = db.execute(select(EmailOutbox.status, func.count()).group_by(EmailOutbox.status)).all()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "database unavailable"})
    return {"status": "ready", "email_queue": {status: n for status, n in rows},
            "booking_mode": settings.booking_mode}


@app.get("/", include_in_schema=False)
def root():
    return {"name": "Event Booking System", "docs": "/docs", "health": "/health"}



@app.get("/debug/email-errors")
def debug_email_errors():
    with SessionLocal() as db:
        rows = db.scalars(
            select(EmailOutbox).where(
                EmailOutbox.status == "failed"
            )
        ).all()

        return [
            {
                "id": row.id,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
            }
            for row in rows
        ]

@app.get("/debug/email-queue")
def debug_email_queue():
    with SessionLocal() as db:
        rows = db.scalars(
            select(EmailOutbox).order_by(EmailOutbox.id)
        ).all()

        return [
            {
                "id": row.id,
                "to_email": row.to_email,
                "status": row.status,
                "attempts": row.attempts,
                "locked_until": str(row.locked_until),
                "last_error": row.last_error,
            }
            for row in rows
        ]