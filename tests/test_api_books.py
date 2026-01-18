# tests/test_api_books.py
from __future__ import annotations

import json

from fastapi import Response
from pydantic import Json
from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.api.routers.books import ResponseMsg
from mywbooks.library import add_book_to_user


def test_add_royalroad_book_by_url(client, db_session: Session, monkeypatch):
    # Pre-create a Book row in the SAME test DB
    b = models.Book(
        provider=(
            models.ProviderKey.ROYALROAD if hasattr(models, "Provider") else "royalroad"
        ),
        provider_fiction_uid="royalroad:21220",
        source_url="https://www.royalroad.com/fiction/21220",
        title="Test RR Book",
        author="Author X",
        language="en",
        cover_url="https://example/cover.jpg",
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)
    created_id = b.id

    # Monkeypatch ingest to just return the known id
    from mywbooks.services import ingest

    async def _fake_func(db, url, dm):
        return created_id

    client.app.state.dm = None
    monkeypatch.setattr(ingest, "upsert_royalroad_book_from_url", _fake_func)

    # Call the API
    resp = client.post("/api/books/royalroad", json={"url": b.source_url})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["id"] == created_id
    assert data["title"] == "Test RR Book"

    # Assert subscription created
    user = db_session.execute(
        select(models.User).where(models.User.auth_subject == "test-user-sub-123")
    ).scalar_one()
    rel = db_session.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == user.id,
            models.BookUser.book_id == created_id,
            models.BookUser.in_library == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    assert rel is not None


def test_add_royalroad_requires_one_of_url_or_fiction_id(client):
    # Missing both -> pydantic model validation should fail with 422
    resp = client.post("/api/books/royalroad", json={})
    assert resp.status_code == 422
    # Optional: check error message content
    body = resp.json()
    # The exact structure can vary by Pydantic/FastAPI, so keep it loose:
    assert "Either 'url' or 'fiction_id' must be provided" in str(body)


def test_list_my_books(client, db_session: Session):
    # Ensure a user + subscription exists (reuse from previous test or create here)
    user = db_session.execute(
        select(models.User).where(models.User.auth_subject == "test-user-sub-123")
    ).scalar_one_or_none()
    if not user:
        user = models.User(
            auth_provider="supabase",
            auth_subject="test-user-sub-123",
            email="tester@example.com",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    b = models.Book(
        provider=(
            models.ProviderKey.ROYALROAD if hasattr(models, "Provider") else "royalroad"
        ),
        provider_fiction_uid="royalroad:999",
        source_url="https://rr/fiction/999",
        title="Another Book",
        author="Z",
        language="en",
        cover_url=None,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)
    add_book_to_user(db_session, user.id, b.id)

    resp = client.get("/api/books")
    assert resp.status_code == 200
    titles = [row["title"] for row in resp.json()]
    assert "Another Book" in titles


def test_unsubscribe_book(client, db_session: Session):
    # Pick any book and ensure relation exists
    book = db_session.execute(select(models.Book)).scalars().first()

    assert book is not None

    user = db_session.execute(
        select(models.User).where(models.User.auth_subject == "test-user-sub-123")
    ).scalar_one()

    rel = db_session.execute(
        select(models.BookUser).where(
            models.BookUser.user_id == user.id, models.BookUser.book_id == book.id
        )
    ).scalar_one_or_none()

    if not rel:
        add_book_to_user(db_session, user.id, book.id)

        rel = db_session.execute(
            select(models.BookUser).where(
                models.BookUser.user_id == user.id, models.BookUser.book_id == book.id
            )
        ).scalar_one_or_none()
    elif rel.in_library is False:
        rel.in_library = True
        db_session.commit()

    assert rel is not None

    resp = client.delete(f"/api/books/{book.id}/unsubscribe")
    assert resp.status_code == 200

    msg = ResponseMsg.model_validate_json(resp.text)
    assert msg.ok

    db_session.refresh(rel)

    assert rel.in_library is False


def test_download_requires_subscription(client, db_session: Session):
    # Create a book with no subscription
    b = models.Book(
        provider=(
            models.ProviderKey.ROYALROAD if hasattr(models, "Provider") else "royalroad"
        ),
        provider_fiction_uid="royalroad:unsub",
        source_url="https://rr/fiction/unsub",
        title="Unsubbed",
        author="N",
        language="en",
        cover_url=None,
    )
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)
    resp = client.post(f"/api/books/{b.id}/download")
    assert resp.status_code == 403
