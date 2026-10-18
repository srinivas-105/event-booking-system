from datetime import datetime, timezone
from decimal import Decimal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes, PostgreSQL returns aware ones. Normalise to aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_dt(dt: datetime) -> str:
    return as_utc(dt).strftime("%d %b %Y, %H:%M UTC")


def fmt_money(value) -> str:
    value = Decimal(value)
    if value == value.to_integral_value():
        return f"₹{int(value):,}"
    return f"₹{value:,.2f}"
