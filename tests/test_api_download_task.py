"""Tests for download body options, task download, and send_by_email endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.library import add_book_to_user

TEST_SUB = "test-user-sub-123"  # must match the session-scoped client fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_create_user(db: Session) -> models.User:
    from sqlalchemy import select
    user = db.execute(
        select(models.User).where(
            models.User.auth_provider == "local",
            models.User.auth_subject == TEST_SUB,
        )
    ).scalar_one_or_none()
    if not user:
        user = models.User(auth_provider="local", auth_subject=TEST_SUB, email="tester@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _create_book(db: Session, uid: str = "dl-book-1") -> models.Book:
    book = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid=uid,
        source_url=f"https://rr/fiction/{uid}",
        title="Download Test Book",
        author="Author",
        language="en",
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def _create_task(
    db: Session,
    user: models.User,
    book: models.Book,
    status: models.TaskStatus = models.TaskStatus.QUEUED,
    output_path: str | None = None,
) -> models.Task:
    payload: dict = {"book_id": book.id}
    if output_path:
        payload["output_path"] = output_path
    task = models.Task(
        type=models.TaskType.DOWNLOAD_BOOK,
        status=status,
        user_id=user.id,
        payload=payload,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# POST /books/{id}/download — body options
# ---------------------------------------------------------------------------


def test_download_with_body_options_stored_in_payload(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "dl-opts-1")
    add_book_to_user(db_session, user.id, book.id)

    r = client.post(f"/api/books/{book.id}/download", json={
        "chapters": [1, 2, 3],
        "title": "Custom Title",
        "cover_img": "https://example.com/cover.jpg",
        "include_images": False,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    task_id = data["task_id"]

    task = db_session.get(models.Task, task_id)
    assert task is not None
    payload = task.payload
    assert payload["chapters"] == [1, 2, 3]
    assert payload["title"] == "Custom Title"
    assert payload["include_images"] is False
    assert payload["book_id"] == book.id


def test_download_without_body_uses_defaults(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "dl-opts-2")
    add_book_to_user(db_session, user.id, book.id)

    r = client.post(f"/api/books/{book.id}/download")
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    task = db_session.get(models.Task, task_id)
    assert task.payload["book_id"] == book.id
    assert task.payload.get("chapters") is None
    assert task.payload.get("title") is None


# ---------------------------------------------------------------------------
# GET /books/tasks/{task_id}/download
# ---------------------------------------------------------------------------


def test_task_download_returns_file(client, db_session):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "dl-file-1")
    add_book_to_user(db_session, user.id, book.id)

    from mywbooks.book import EPUB_DIR
    with tempfile.NamedTemporaryFile(suffix=".epub", dir=EPUB_DIR, delete=False) as f:
        f.write(b"fake epub content")
        epub_path = f.name

    task = _create_task(db_session, user, book, models.TaskStatus.SUCCEEDED, epub_path)

    r = client.get(f"/api/books/tasks/{task.id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"

    Path(epub_path).unlink(missing_ok=True)


def test_task_download_not_found(client, db_session):
    r = client.get("/api/books/tasks/999999/download")
    assert r.status_code == 404


def test_task_download_not_finished(client, db_session):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "dl-nf-1")
    add_book_to_user(db_session, user.id, book.id)
    task = _create_task(db_session, user, book, models.TaskStatus.RUNNING)

    r = client.get(f"/api/books/tasks/{task.id}/download")
    assert r.status_code == 400


def test_task_download_missing_output_file(client, db_session):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "dl-missing-1")
    add_book_to_user(db_session, user.id, book.id)

    from mywbooks.book import EPUB_DIR
    task = _create_task(
        db_session, user, book, models.TaskStatus.SUCCEEDED,
        str(EPUB_DIR / "nonexistent-file.epub"),
    )

    r = client.get(f"/api/books/tasks/{task.id}/download")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /books/tasks/{task_id}/send_by_email
# ---------------------------------------------------------------------------


def test_send_by_email_queued_task_updates_payload(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    user.kindle_email = "test@kindle.com"
    db_session.commit()

    book = _create_book(db_session, "sbe-queued-1")
    add_book_to_user(db_session, user.id, book.id)
    task = _create_task(db_session, user, book, models.TaskStatus.QUEUED)

    r = client.get(f"/api/books/tasks/{task.id}/send_by_email")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    db_session.refresh(task)
    assert task.payload.get("send_by_email") is not None
    assert task.payload["send_by_email"]["recipient_email"] == "test@kindle.com"


def test_send_by_email_succeeded_task_queues_send_task(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    user.kindle_email = "test@kindle.com"
    db_session.commit()

    book = _create_book(db_session, "sbe-done-1")
    add_book_to_user(db_session, user.id, book.id)

    from mywbooks.book import EPUB_DIR
    with tempfile.NamedTemporaryFile(suffix=".epub", dir=EPUB_DIR, delete=False) as f:
        f.write(b"epub")
        epub_path = f.name

    task = _create_task(db_session, user, book, models.TaskStatus.SUCCEEDED, epub_path)

    r = client.get(f"/api/books/tasks/{task.id}/send_by_email")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    # A new SEND_BOOK task should have been created
    assert data["task_id"] != task.id

    Path(epub_path).unlink(missing_ok=True)


def test_send_by_email_failed_task_returns_406(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    user.kindle_email = "test@kindle.com"
    db_session.commit()

    book = _create_book(db_session, "sbe-failed-1")
    add_book_to_user(db_session, user.id, book.id)
    task = _create_task(db_session, user, book, models.TaskStatus.FAILED)

    r = client.get(f"/api/books/tasks/{task.id}/send_by_email")
    assert r.status_code == 406


def test_send_by_email_no_kindle_email_returns_400(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    user.kindle_email = None
    db_session.commit()

    book = _create_book(db_session, "sbe-noemail-1")
    add_book_to_user(db_session, user.id, book.id)
    task = _create_task(db_session, user, book, models.TaskStatus.QUEUED)

    r = client.get(f"/api/books/tasks/{task.id}/send_by_email")
    assert r.status_code == 400


def test_send_by_email_recipient_override(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    user.kindle_email = None  # no default
    db_session.commit()

    book = _create_book(db_session, "sbe-override-1")
    add_book_to_user(db_session, user.id, book.id)
    task = _create_task(db_session, user, book, models.TaskStatus.QUEUED)

    r = client.get(
        f"/api/books/tasks/{task.id}/send_by_email",
        params={"recipient_email": "override@kindle.com"},
    )
    assert r.status_code == 200
    db_session.refresh(task)
    assert task.payload["send_by_email"]["recipient_email"] == "override@kindle.com"


# ---------------------------------------------------------------------------
# Medium priority
# ---------------------------------------------------------------------------


def test_unsubscribe_is_idempotent(client, db_session):
    user = _get_or_create_user(db_session)
    book = _create_book(db_session, "unsub-idem-1")
    add_book_to_user(db_session, user.id, book.id)

    r1 = client.delete(f"/api/books/{book.id}/unsubscribe")
    assert r1.status_code == 200
    r2 = client.delete(f"/api/books/{book.id}/unsubscribe")
    assert r2.status_code == 200


def test_put_device_invalid_email_returns_422(client):
    r = client.put("/api/user/device", json={"kindle_email": "not-an-email"})
    assert r.status_code == 422
