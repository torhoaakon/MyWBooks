"""Tests for per-chapter delivery tracking (T-057).

Covers: dismiss/undismiss endpoints, new_chapter_count on book list,
and cross-user protection for dismiss endpoints.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.library import add_book_to_user
from mywbooks.utils import utcnow

_uid_counter = iter(range(10_000, 20_000))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(db_session: Session, uid: str) -> models.Book:
    book = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid=uid,
        source_url=f"https://rr/fiction/{uid}",
        title="Delivery Test Book",
        author="Author",
        language="en",
    )
    db_session.add(book)
    db_session.commit()
    db_session.refresh(book)
    return book


def _make_chapter(
    db_session: Session, book_id: int, index: int, **kwargs
) -> models.Chapter:
    ch = models.Chapter(
        book_id=book_id,
        index=index,
        title=f"Chapter {index + 1}",
        provider_chapter_id=f"rr:{book_id}:{index}",
        source_url=f"https://rr/chapter/{book_id}/{index}",
        is_fetched=False,
        **kwargs,
    )
    db_session.add(ch)
    db_session.commit()
    db_session.refresh(ch)
    return ch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def setup(client: TestClient, db_session: Session):
    """One book owned by the default test user with three chapters."""
    from mywbooks.api.auth import get_or_create_user_by_sub

    user = get_or_create_user_by_sub(
        db_session,
        {
            "sub": "test-user-sub-123",
            "email": "tester@example.com",
        },
    )
    book = _make_book(db_session, f"delivery-{next(_uid_counter)}")
    add_book_to_user(db_session, user.id, book.id)
    ch1 = _make_chapter(db_session, book.id, 0)
    ch2 = _make_chapter(db_session, book.id, 1)
    ch3 = _make_chapter(db_session, book.id, 2, delivered_at=utcnow())
    return {"book": book, "ch1": ch1, "ch2": ch2, "ch3": ch3}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_new_chapter_count_excludes_delivered(client: TestClient, setup):
    book = setup["book"]
    resp = client.get("/api/books")
    assert resp.status_code == 200
    book_data = next(b for b in resp.json() if b["id"] == book.id)
    # ch1 and ch2 are new, ch3 is delivered → count should be 2
    assert book_data["new_chapter_count"] == 2


def test_new_chapter_count_excludes_dismissed(
    client: TestClient, db_session: Session, setup
):
    book = setup["book"]
    ch2 = setup["ch2"]
    ch2.dismissed_at = utcnow()
    db_session.commit()

    resp = client.get("/api/books")
    assert resp.status_code == 200
    book_data = next(b for b in resp.json() if b["id"] == book.id)
    # ch1 is the only new one now
    assert book_data["new_chapter_count"] == 1

    ch2.dismissed_at = None
    db_session.commit()


def test_dismiss_sets_dismissed_at(client: TestClient, setup):
    book = setup["book"]
    ch1 = setup["ch1"]
    resp = client.post(f"/api/books/{book.id}/chapters/{ch1.id}/dismiss")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dismissed_at"] is not None
    assert data["delivered_at"] is None


def test_undismiss_clears_dismissed_at(client: TestClient, db_session: Session, setup):
    book = setup["book"]
    ch1 = setup["ch1"]
    ch1.dismissed_at = utcnow()
    db_session.commit()

    resp = client.post(f"/api/books/{book.id}/chapters/{ch1.id}/undismiss")
    assert resp.status_code == 200
    assert resp.json()["dismissed_at"] is None


def test_chapter_list_includes_delivery_fields(client: TestClient, setup):
    book = setup["book"]
    resp = client.get(f"/api/books/{book.id}/chapters")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert "delivered_at" in item
        assert "dismissed_at" in item


def test_dismiss_wrong_book_returns_404(client: TestClient, setup):
    ch1 = setup["ch1"]
    resp = client.post(f"/api/books/99999/chapters/{ch1.id}/dismiss")
    assert resp.status_code == 404


def test_dismiss_wrong_chapter_returns_404(client: TestClient, setup):
    book = setup["book"]
    resp = client.post(f"/api/books/{book.id}/chapters/99999/dismiss")
    assert resp.status_code == 404
