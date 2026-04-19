# tests/conftest.py
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from arq.connections import ArqRedis
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mywbooks.api.app import app
from mywbooks.api.deps import get_arq_pool
from mywbooks.models import Base
from tests.fakes import FakeAsyncDownloadManager

# One shared in-memory SQLite for all sessions/connections
engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Create all tables once for the test DB
Base.metadata.create_all(bind=engine)


def _test_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _fake_current_user():
    return {
        "sub": "test-user-sub-123",
        "email": "tester@example.com",
        "iss": "https://test-issuer.example/auth/v1",
        "aud": "authenticated",
        "role": "authenticated",
    }


@pytest.fixture(scope="session")
def client():
    # 1) override DB dependency used by the books router:
    from mywbooks import db

    app.dependency_overrides[db.get_db] = _test_get_db

    # 2) override CurrentUser dependency:
    from mywbooks.api.auth import verify_jwt

    app.dependency_overrides[verify_jwt] = lambda: _fake_current_user()

    # 3) Mock ARQ pool
    mock_arq = MagicMock(spec=ArqRedis)
    mock_arq.enqueue_job = MagicMock(return_value=None)
    app.dependency_overrides[get_arq_pool] = lambda: mock_arq

    return TestClient(app)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Give tests a direct handle to the same in-memory DB the app uses."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_user_claims(sub: str, email: str) -> dict:
    return {
        "sub": sub,
        "email": email,
        "iss": "https://test-issuer.example/auth/v1",
        "aud": "authenticated",
        "role": "authenticated",
    }


@pytest.fixture
def make_client():
    """Return a factory that creates a TestClient authenticated as any user.

    The fixture restores the original verify_jwt override after the test so
    the session-scoped `client` fixture is unaffected.
    """
    from unittest.mock import AsyncMock

    from mywbooks import db as db_module
    from mywbooks.api.auth import verify_jwt

    saved = app.dependency_overrides.get(verify_jwt)

    def _factory(sub: str, email: str | None = None) -> TestClient:
        email = email or f"{sub}@test.example"
        mock_arq = MagicMock(spec=ArqRedis)
        mock_arq.enqueue_job = AsyncMock(return_value=None)
        claims = _make_user_claims(sub, email)
        app.dependency_overrides[db_module.get_db] = _test_get_db
        app.dependency_overrides[verify_jwt] = lambda: claims
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq
        return TestClient(app)

    yield _factory

    # Restore the session-scoped user after the test
    if saved is not None:
        app.dependency_overrides[verify_jwt] = saved


@pytest.fixture
def mock_arq_pool():
    mock = MagicMock(spec=ArqRedis)
    # enqueue_job needs to be awaitable in async endpoints,
    # but since we mock the dependency which is async,
    # and MagicMock isn't awaitable by default unless configured.
    # However, in sync tests with TestClient, it mostly matters what the endpoint does.
    # If the endpoint awaits it, the mock needs to return an awaitable or be an AsyncMock.

    # Simpler: TestClient runs async endpoints via generic asyncio loop.
    # We should use AsyncMock for async methods.
    from unittest.mock import AsyncMock

    mock.enqueue_job = AsyncMock(return_value=None)

    app.dependency_overrides[get_arq_pool] = lambda: mock
    return mock
