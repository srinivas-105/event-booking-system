
"""All configuration comes from environment variables (or a local .env file)."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # core
    environment: str = "development"
    database_url: str = "sqlite:///./event_booking.db"
    secret_key: str = DEV_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    bcrypt_rounds: int = 12
    organizer_invite_code: str = ""

    # booking behaviour
    booking_mode: str = "optimized"
    max_tickets_per_booking: int = 10

    # performance knobs
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    threadpool_size: int = 40
    max_concurrent_requests: int = 0

    # email
    email_provider: str = "resend"  # "resend" | "gmail" | "mock"
    resend_api_key: str = ""
    email_from: str = "Event Booking Team <onboarding@resend.dev>"

    # Gmail SMTP credentials
    gmail_email: str = ""
    gmail_app_password: str = ""

    email_mock_latency_ms: int = 0
    email_http_timeout: float = 10.0
    run_email_worker: bool = True
    email_poll_seconds: float = 2.0
    email_batch_size: int = 20
    email_max_attempts: int = 5

    @property
    def request_limit(self) -> int:
        if self.max_concurrent_requests > 0:
            return self.max_concurrent_requests
        return max(1, self.db_pool_size + self.db_max_overflow - 2)

    @property
    def sqlalchemy_url(self) -> str:
        """Render hands out postgres:// URLs; SQLAlchemy needs postgresql+psycopg2://."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.booking_mode not in {"baseline", "optimized"}:
            raise ValueError("BOOKING_MODE must be 'baseline' or 'optimized'")

        if self.email_provider not in {"resend", "gmail", "mock"}:
            raise ValueError("EMAIL_PROVIDER must be 'resend', 'gmail', or 'mock'")

        if self.environment != "development" and (
            self.secret_key == DEV_SECRET or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be a random string of 32+ characters outside development"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
