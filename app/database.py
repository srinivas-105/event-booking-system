import asyncio
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()
url = settings.sqlalchemy_url
IS_SQLITE = url.startswith("sqlite")

if IS_SQLITE:
    # SQLite is only for quick local runs and unit tests.
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
    )

# expire_on_commit=False: after commit we can still read attributes without an extra SELECT.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Sessions are closed on their OWN small executor, not on FastAPI's request threadpool.
# Why: with a sync `get_db`, closing the session needs a free request thread. Under a burst every request
# thread can be blocked waiting for a DB connection that is still held by a finished request whose
# cleanup is waiting for a free thread -> deadlock until the pool times out. (Found with k6, 100 VUs.)
_close_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db-close")


async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        await asyncio.get_running_loop().run_in_executor(_close_pool, db.close)
