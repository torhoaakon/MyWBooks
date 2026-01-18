from __future__ import annotations

import json
from unittest.mock import ANY, MagicMock

import pytest
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.models import TaskStatus, TaskType


def test_download_book_endpoint_schedules_task(
    client, db_session: Session, mock_arq_pool
):
    # 1. Setup: Create a user and a book subscription
    # Note: client fixture already mocks authentication as "tester@example.com"
    # We need to make sure this user exists in the DB or is created by the endpoint dependencies.
    # The `get_or_create_user_by_sub` helper handles this.

    # We manually create the book and subscription to ensure state.
    # We first need to know the ID of the user the mock auth returns.
    # Since we can't easily peek into the "me" endpoint without a request,
    # let's just use the known sub from conftest.

    # Trigger user creation via a harmless endpoint
    a = client.get("/api/profile")
    print(a)

    user = (
        db_session.query(models.User)
        # .filter_by(auth_subject="test-user-sub-123")
        .first()
    )
    assert user is not None

    book = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid="12345",
        source_url="https://rr.com/12345",
        title="Test Book",
        language="en",
    )
    db_session.add(book)
    db_session.commit()

    # Subscribe
    bu = models.BookUser(user_id=user.id, book_id=book.id, in_library=True)
    db_session.add(bu)
    db_session.commit()

    # 2. Action: Call the download endpoint
    response = client.post(
        f"/api/books/{book.id}/download", json={"include_images": False}
    )

    # 3. Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    task_id = data["task_id"]

    # Verify Task created in DB
    task = db_session.get(models.Task, task_id)
    assert task is not None
    assert task.type == TaskType.DOWNLOAD_BOOK
    assert task.status == TaskStatus.QUEUED

    assert task.payload is not None
    assert task.payload["book_id"] == book.id
    assert task.payload["include_images"] is False

    # Verify Arq job enqueued
    mock_arq_pool.enqueue_job.assert_called_once_with("download_book_task", task.id)


def test_send_email_endpoint_schedules_task(client, db_session: Session, mock_arq_pool):
    # 1. Setup
    client.get("/api/profile")
    user = (
        db_session.query(models.User)
        .filter_by(auth_subject="test-user-sub-123")
        .first()
    )
    user.kindle_email = "kindle@example.com"
    db_session.commit()

    # Create a completed download task
    task = models.Task(
        user_id=user.id,
        type=TaskType.DOWNLOAD_BOOK,
        status=TaskStatus.SUCCEEDED,
        payload={"book_id": 999, "output_path": "/tmp/fake_book.epub"},
    )
    db_session.add(task)
    db_session.commit()

    # Mock file existence check (pathlib.Path.is_file)
    # Since the endpoint checks `path.is_file()`, we need to ensure it passes or mock it.
    # However, the endpoint also checks `relative_to(EPUB_DIR)`.
    # Let's use a path that satisfies the relative check.
    from mywbooks.book import EPUB_DIR

    valid_path = EPUB_DIR / "fake_book.epub"

    # Update task payload with valid path
    task.payload = {"book_id": 999, "output_path": str(valid_path)}
    db_session.commit()

    # We need to mock Path.is_file. Since we can't easily mock standard library objects
    # instantiated inside the function without patching, we'll patch `pathlib.Path.is_file` globally for this test context.
    with pytest.MonkeyPatch.context() as m:
        m.setattr("pathlib.Path.is_file", lambda self: True)

        # 2. Action
        response = client.get(f"/api/books/tasks/{task.id}/send_by_email")

    print(response)

    # 3. Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True

    # Verify new Send Task created
    send_task_id = data["task_id"]
    send_task = db_session.get(models.Task, send_task_id)
    assert send_task is not None
    assert send_task.type == TaskType.SEND_BOOK

    assert send_task.payload is not None
    assert send_task.payload["recipient_email"] == "kindle@example.com"

    # Verify Arq job enqueued
    mock_arq_pool.enqueue_job.assert_called_with("send_book_task", send_task.id)
