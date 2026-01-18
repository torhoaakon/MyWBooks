# tests/conftest.py
from __future__ import annotations

from collections.abc import Iterator
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from arq.connections import ArqRedis
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mywbooks.api.app import app
from mywbooks.api.deps import get_arq_pool
from mywbooks.models import Base

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