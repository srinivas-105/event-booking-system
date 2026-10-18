from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.config import get_settings
from app.utils import utcnow

settings = get_settings()


# ---------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt only uses the first 72 bytes
    role: Literal["customer", "organizer"] = "customer"
    invite_code: str | None = Field(default=None, max_length=200, description="Required only for role=organizer")

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("password must be at most 72 bytes")
        if not (any(c.isalpha() for c in v) and any(c.isdigit() for c in v)):
            raise ValueError("password must contain at least one letter and one digit")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr
    role: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="seconds")


# ---------------------------------------------------------------- events
def _to_utc_future(v: datetime) -> datetime:
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)  # naive input is treated as UTC
    v = v.astimezone(timezone.utc)
    if v <= utcnow():
        raise ValueError("event_date must be in the future")
    return v


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=5000)
    venue: str = Field(min_length=2, max_length=255)
    event_date: datetime
    ticket_price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    capacity: int = Field(ge=1, le=10_000_000)

    @field_validator("title", "venue")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("event_date")
    @classmethod
    def _future(cls, v: datetime) -> datetime:
        return _to_utc_future(v)


class EventUpdate(BaseModel):
    """Partial update: send only the fields you want to change."""

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    venue: str | None = Field(default=None, min_length=2, max_length=255)
    event_date: datetime | None = None
    ticket_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    capacity: int | None = Field(default=None, ge=1, le=10_000_000)

    @field_validator("title", "venue")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("event_date")
    @classmethod
    def _future(cls, v):
        return _to_utc_future(v) if v is not None else v

    @model_validator(mode="after")
    def _no_nulls_and_not_empty(self):
        sent = self.model_fields_set
        if not sent:
            raise ValueError("send at least one field to update")
        for name in sent:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    venue: str
    event_date: datetime
    ticket_price: Decimal
    capacity: int
    available_tickets: int
    organizer_id: int
    created_at: datetime


class EventBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    venue: str
    event_date: datetime


# ---------------------------------------------------------------- bookings
class BookingCreate(BaseModel):
    quantity: int = Field(ge=1, le=settings.max_tickets_per_booking)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    event_id: int
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    status: str
    created_at: datetime
    event: EventBrief


class CustomerBrief(BaseModel):  # deliberately has NO password / token fields
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr


class OrganizerBookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    status: str
    created_at: datetime
    customer: CustomerBrief


class BookingSummary(BaseModel):
    capacity: int
    available_tickets: int
    confirmed_bookings: int
    confirmed_tickets: int
    revenue: Decimal
    inventory_consistent: bool = Field(
        description="True when confirmed_tickets == capacity - available_tickets and nothing is oversold. "
        "Read it after traffic stops: during a live burst the two reads are not one snapshot."
    )


class OrganizerEventBookings(BaseModel):
    event: EventOut
    summary: BookingSummary
    bookings: list[OrganizerBookingOut]
