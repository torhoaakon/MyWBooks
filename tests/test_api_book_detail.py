# tests/test_api_book_detail.py
"""Tests for the book detail, chapters, tasks, options, and send endpoints."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.library import add_book_to_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(db: Session, uid: str, title: str = "Test Book") -> models.Book:
    b = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid=uid,
        source_url=f"https://rr/fiction/{uid}",
        title=title,
        author="Author",
        language="en",
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _make_chapters(db: Session, book_id: int, n: int) -> list[models.Chapter]:
    chapters = [
        models.Chapter(
            book_id=book_id,
            index=i,
            title=f"Chapter {i}",
            provider_chapter_id=f"ch-{book_id}-{i}",
            source_url=f"https://rr/chapter/{book_id}/{i}",
            is_fetched=False,
        )
        for i in range(1, n + 1)
    ]
    db.add_all(chapters)
    db.commit()
    return chapters


def _get_or_create_test_user(db: Session) -> models.User:
    # auth_provider must be "local" — the fake JWT claims have no "provider" key
    # so get_or_create_user_by_sub defaults to "local".
    user = db.execute(
        select(models.User).where(
            models.User.auth_provider == "local",
            models.User.auth_subject == "test-user-sub-123",
        )
    ).scalar_one_or_none()
    if not user:
        user = models.User(
            auth_provider="local",
            auth_subject="test-user-sub-123",
            email="tester@example.com",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _subscribed_book(db: Session, uid: str, title: str = "Test Book") -> models.Book:
    """Create a book and subscribe the test user to it."""
    book = _make_book(db, uid, title)
    user = _get_or_create_test_user(db)
    add_book_to_user(db, user.id, book.id)
    return book


# ---------------------------------------------------------------------------
# GET /books/{id}
# ---------------------------------------------------------------------------


def test_get_book_detail_returns_metadata(client, db_session):
    book = _subscribed_book(db_session, "detail-001", "Detail Book")
    _make_chapters(db_session, book.id, 5)

    resp = client.get(f"/api/books/{book.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == book.id
    assert data["title"] == "Detail Book"
    assert data["chapter_count"] == 5


def test_get_book_detail_default_options(client, db_session):
    book = _subscribed_book(db_session, "detail-002")

    resp = client.get(f"/api/books/{book.id}")
    assert resp.status_code == 200
    opts = resp.json()["user_options"]
    assert opts["include_images"] is True
    assert opts["include_chapter_titles"] is True


def test_get_book_detail_not_subscribed_returns_404(client, db_session):
    book = _make_book(db_session, "detail-nosub")

    resp = client.get(f"/api/books/{book.id}")
    assert resp.status_code == 404


def test_get_book_detail_unknown_id_returns_404(client):
    resp = client.get("/api/books/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /books/{id}/chapters
# ---------------------------------------------------------------------------


def test_get_chapters_returns_all(client, db_session):
    book = _subscribed_book(db_session, "chapters-001")
    _make_chapters(db_session, book.id, 10)

    resp = client.get(f"/api/books/{book.id}/chapters")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 10
    assert len(data["items"]) == 10


def test_get_chapters_paging(client, db_session):
    book = _subscribed_book(db_session, "chapters-002")
    _make_chapters(db_session, book.id, 10)

    resp = client.get(f"/api/books/{book.id}/chapters?page=2&page_size=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 10
    assert data["page"] == 2
    assert len(data["items"]) == 3


def test_get_chapters_sort_desc(client, db_session):
    book = _subscribed_book(db_session, "chapters-003")
    _make_chapters(db_session, book.id, 5)

    resp = client.get(f"/api/books/{book.id}/chapters?sort=desc")
    assert resp.status_code == 200
    indices = [ch["index"] for ch in resp.json()["items"]]
    assert indices == sorted(indices, reverse=True)


def test_get_chapters_not_subscribed_returns_404(client, db_session):
    book = _make_book(db_session, "chapters-nosub")

    resp = client.get(f"/api/books/{book.id}/chapters")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /books/{id}/tasks
# ---------------------------------------------------------------------------


def test_get_book_tasks_empty(client, db_session):
    book = _subscribed_book(db_session, "tasks-001")

    resp = client.get(f"/api/books/{book.id}/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_book_tasks_returns_matching_tasks(client, db_session):
    book = _subscribed_book(db_session, "tasks-002")
    user = _get_or_create_test_user(db_session)

    task = models.Task(
        type=models.TaskType.DOWNLOAD_BOOK,
        status=models.TaskStatus.QUEUED,
        user_id=user.id,
        payload={"book_id": book.id},
    )
    db_session.add(task)
    db_session.commit()

    resp = client.get(f"/api/books/{book.id}/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == task.id
    assert data[0]["status"] == "queued"


def test_get_book_tasks_excludes_other_books(client, db_session):
    book_a = _subscribed_book(db_session, "tasks-003a")
    book_b = _subscribed_book(db_session, "tasks-003b")
    user = _get_or_create_test_user(db_session)

    task_b = models.Task(
        type=models.TaskType.DOWNLOAD_BOOK,
        status=models.TaskStatus.QUEUED,
        user_id=user.id,
        payload={"book_id": book_b.id},
    )
    db_session.add(task_b)
    db_session.commit()

    resp = client.get(f"/api/books/{book_a.id}/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# PUT /books/{id}/options
# ---------------------------------------------------------------------------


def test_save_options_persists(client, db_session):
    book = _subscribed_book(db_session, "opts-001")

    resp = client.put(
        f"/api/books/{book.id}/options",
        json={"include_images": False, "include_chapter_titles": True,
              "include_description": False, "custom_title": "My Title",
              "custom_cover_url": None},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify persisted
    resp2 = client.get(f"/api/books/{book.id}")
    opts = resp2.json()["user_options"]
    assert opts["include_images"] is False
    assert opts["custom_title"] == "My Title"


def test_save_options_not_subscribed_returns_404(client, db_session):
    book = _make_book(db_session, "opts-nosub")

    resp = client.put(
        f"/api/books/{book.id}/options",
        json={"include_images": True, "include_chapter_titles": True,
              "include_description": False, "custom_title": None,
              "custom_cover_url": None},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /books/{id}/send
# ---------------------------------------------------------------------------


def test_send_to_device_no_kindle_email_returns_400(client, db_session):
    book = _subscribed_book(db_session, "send-001")
    # Ensure user has no kindle_email
    user = _get_or_create_test_user(db_session)
    user.kindle_email = None
    db_session.commit()

    resp = client.post(f"/api/books/{book.id}/send")
    assert resp.status_code == 400
    assert "device email" in resp.json()["detail"].lower()


def test_send_to_device_queues_task(client, db_session, mock_arq_pool):
    book = _subscribed_book(db_session, "send-002")
    user = _get_or_create_test_user(db_session)
    user.kindle_email = "test@kindle.com"
    db_session.commit()

    resp = client.post(f"/api/books/{book.id}/send")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_status"] == "queued"


def test_send_to_device_not_subscribed_returns_403(client, db_session):
    book = _make_book(db_session, "send-nosub")
    user = _get_or_create_test_user(db_session)
    user.kindle_email = "test@kindle.com"
    db_session.commit()

    resp = client.post(f"/api/books/{book.id}/send")
    assert resp.status_code == 403
