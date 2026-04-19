"""Cross-user access control tests.

Every book/task/device endpoint that is scoped to the authenticated user must
return 404 (or 403 for the download endpoint) when a different user attempts
to access it — even if they know the resource ID.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.library import add_book_to_user

USER1_SUB = "user1-sub-aaa"
USER2_SUB = "user2-sub-bbb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_user(db: Session, sub: str) -> models.User:
    user = models.User(auth_provider="local", auth_subject=sub, email=f"{sub}@test.example")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_book(db: Session, uid: str) -> models.Book:
    book = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid=uid,
        source_url=f"https://rr/fiction/{uid}",
        title="Cross-User Book",
        author="Author",
        language="en",
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def _create_task(db: Session, user: models.User, book: models.Book) -> models.Task:
    task = models.Task(
        type=models.TaskType.DOWNLOAD_BOOK,
        status=models.TaskStatus.QUEUED,
        user_id=user.id,
        payload={"book_id": book.id},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_book_detail_other_user_returns_404(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB)
    book = _create_book(db_session, "xac-detail")
    add_book_to_user(db_session, user1.id, book.id)

    c2 = make_client(USER2_SUB)
    r = c2.get(f"/api/books/{book.id}")
    assert r.status_code == 404


def test_get_chapters_other_user_returns_404(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "ch")
    book = _create_book(db_session, "xac-chapters")
    add_book_to_user(db_session, user1.id, book.id)

    c2 = make_client(USER2_SUB + "ch")
    r = c2.get(f"/api/books/{book.id}/chapters")
    assert r.status_code == 404


def test_get_book_tasks_other_user_returns_404(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "tk")
    book = _create_book(db_session, "xac-tasks")
    add_book_to_user(db_session, user1.id, book.id)

    c2 = make_client(USER2_SUB + "tk")
    r = c2.get(f"/api/books/{book.id}/tasks")
    assert r.status_code == 404


def test_save_options_other_user_returns_404(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "opt")
    book = _create_book(db_session, "xac-options")
    add_book_to_user(db_session, user1.id, book.id)

    c2 = make_client(USER2_SUB + "opt")
    r = c2.put(f"/api/books/{book.id}/options", json={"include_images": False, "include_chapter_titles": True, "include_description": False})
    assert r.status_code == 404


def test_download_other_users_book_returns_403(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "dl")
    book = _create_book(db_session, "xac-download")
    add_book_to_user(db_session, user1.id, book.id)

    c2 = make_client(USER2_SUB + "dl")
    r = c2.post(f"/api/books/{book.id}/download")
    assert r.status_code == 403


def test_send_other_users_book_returns_403(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "snd")
    book = _create_book(db_session, "xac-send")
    add_book_to_user(db_session, user1.id, book.id)

    # user2 needs a kindle_email set or we'd get 400 first
    user2 = _create_user(db_session, USER2_SUB + "snd")
    user2.kindle_email = "user2@kindle.com"
    db_session.commit()

    c2 = make_client(USER2_SUB + "snd")
    r = c2.post(f"/api/books/{book.id}/send")
    assert r.status_code == 403


def test_download_task_wrong_owner_returns_404(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "dtask")
    book = _create_book(db_session, "xac-dtask")
    add_book_to_user(db_session, user1.id, book.id)
    task = _create_task(db_session, user1, book)

    c2 = make_client(USER2_SUB + "dtask")
    r = c2.get(f"/api/books/tasks/{task.id}/download")
    assert r.status_code == 404


def test_device_config_is_isolated_per_user(make_client, db_session):
    user1 = _create_user(db_session, USER1_SUB + "dev")
    user1.kindle_email = "user1@kindle.com"
    db_session.commit()

    c2 = make_client(USER2_SUB + "dev")
    r = c2.get("/api/user/device")
    assert r.status_code == 200
    # user2 has no kindle email — should not see user1's
    assert r.json().get("kindle_email") is None
