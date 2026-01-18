from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from redis.asyncio import retry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mywbooks.api import app
from mywbooks.async_download_manager import AsyncDownloadManager

# Dev: SQLite file; prod: switch to Postgres
DATABASE_URL = "sqlite:///./mywbooks.db"

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    from .models import Base  # register models

    Base.metadata.create_all(bind=engine)


# Only intended to be used by FastAPI
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
