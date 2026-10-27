import os
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def configured_database_url() -> str:
    """Build the database URL from a mounted password secret when configured."""
    password_file = os.getenv("DATABASE_PASSWORD_FILE", "").strip()
    if not password_file:
        return os.getenv("DATABASE_URL", "sqlite:///./orchestra.db")
    try:
        password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
    except FileNotFoundError:
        # Managed hosting platforms may provide DATABASE_URL but not mount
        # Docker Compose secrets. Keep the file-based path for Compose while
        # allowing the platform-provided URL when that mount is unavailable.
        database_url = os.getenv("DATABASE_URL", "")
        if database_url:
            return database_url
        raise RuntimeError("Database password secret file is missing") from None
    if not password:
        raise RuntimeError("Database password secret is empty")
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("DATABASE_USER", "orchestra"),
        password=password,
        host=os.getenv("DATABASE_HOST", "postgres"),
        port=int(os.getenv("DATABASE_PORT", "5432")),
        database=os.getenv("DATABASE_NAME", "orchestra"),
    ).render_as_string(hide_password=False)


DATABASE_URL = configured_database_url()
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db
