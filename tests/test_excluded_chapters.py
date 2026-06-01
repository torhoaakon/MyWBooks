"""Tests for the excluded_chapters selection feature.

Covers two layers:
- API: POST /books/{id}/download correctly stores excluded_chapters in the task payload.
- Resolution logic: the chapter-id query in download_book_task produces the right set.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.library import add_book_to_user

TEST_SUB = "test-user-sub-123"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_or_create_user(db: Session) -> models.User:
    user = db.execute(
        select(models.User).where(
            models.User.auth_provider == "local",
            models.User.auth_subject == TEST_SUB,
        )
    ).scalar_one_or_none()
    if not user:
        user = models.User(
            auth_provider="local",
            auth_subject=TEST_SUB,
            email="tester@example.com",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _make_book_with_chapters(
    db: Session, uid: str, n: int
) -> tuple[models.Book, list[models.Chapter]]:
    book = models.Book(
        provider=models.ProviderKey.ROYALROAD,
        provider_fiction_uid=uid,
        source_url=f"https://rr/fiction/{uid}",
        title="Test Book",
        language="en",
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    for i in range(n):
        db.add(
            models.Chapter(
                book_id=book.id,
                index=i,
                title=f"Chapter {i + 1}",
                provider_chapter_id=str(9000 + hash(uid) % 1000 + i),
                source_url=f"https://rr/fiction/{uid}/chapter/{i + 1}",
                is_fetched=False,
            )
        )
    db.commit()

    chapters = (
        db.execute(
            select(models.Chapter)
            .where(models.Chapter.book_id == book.id)
            .order_by(models.Chapter.index)
        )
        .scalars()
        .all()
    )
    return book, list(chapters)


def _resolve(
    db: Session, book: models.Book, payload: models.DownloadBookTaskPayload
) -> list[int] | None:
    """Mirrors the effective_chapter_ids resolution block in download_book_task."""
    if payload.chapters:
        return payload.chapters
    if payload.excluded_chapters:
        rows = (
            db.execute(
                select(models.Chapter.id).where(
                    models.Chapter.book_id == book.id,
                    ~models.Chapter.id.in_(payload.excluded_chapters),
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
    return None


# ---------------------------------------------------------------------------
# API layer: payload storage
# ---------------------------------------------------------------------------


def test_excluded_chapters_stored_in_task_payload(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    book, chapters = _make_book_with_chapters(db_session, "excl-api-1", 5)
    add_book_to_user(db_session, user.id, book.id)

    excluded = [chapters[1].id, chapters[3].id]
    r = client.post(
        f"/api/books/{book.id}/download", json={"excluded_chapters": excluded}
    )
    assert r.status_code == 200

    task = db_session.get(models.Task, r.json()["task_id"])
    assert task.payload.get("excluded_chapters") == excluded
    assert task.payload.get("chapters") is None


def test_explicit_chapters_stored_without_excluded(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    book, chapters = _make_book_with_chapters(db_session, "excl-api-2", 5)
    add_book_to_user(db_session, user.id, book.id)

    ch_ids = [chapters[0].id, chapters[2].id]
    r = client.post(f"/api/books/{book.id}/download", json={"chapters": ch_ids})
    assert r.status_code == 200

    task = db_session.get(models.Task, r.json()["task_id"])
    assert task.payload["chapters"] == ch_ids
    assert task.payload.get("excluded_chapters") is None


def test_neither_chapters_nor_excluded_means_all(client, db_session, mock_arq_pool):
    user = _get_or_create_user(db_session)
    book, _ = _make_book_with_chapters(db_session, "excl-api-3", 5)
    add_book_to_user(db_session, user.id, book.id)

    r = client.post(f"/api/books/{book.id}/download", json={})
    assert r.status_code == 200

    task = db_session.get(models.Task, r.json()["task_id"])
    assert task.payload.get("chapters") is None
    assert task.payload.get("excluded_chapters") is None


# ---------------------------------------------------------------------------
# Resolution logic: effective chapter id computation
# ---------------------------------------------------------------------------


def test_resolution_explicit_chapters_returned_as_is(db_session):
    book, chapters = _make_book_with_chapters(db_session, "res-explicit-1", 5)
    subset = [chapters[0].id, chapters[2].id, chapters[4].id]
    payload = models.DownloadBookTaskPayload(book_id=book.id, chapters=subset)

    assert _resolve(db_session, book, payload) == subset


def test_resolution_excluded_removes_specified_chapters(db_session):
    book, chapters = _make_book_with_chapters(db_session, "res-excl-1", 5)
    excluded = [chapters[1].id, chapters[3].id]
    payload = models.DownloadBookTaskPayload(
        book_id=book.id, excluded_chapters=excluded
    )

    result = _resolve(db_session, book, payload)
    assert result is not None
    assert set(result) == {chapters[0].id, chapters[2].id, chapters[4].id}


def test_resolution_no_filter_returns_none(db_session):
    book, _ = _make_book_with_chapters(db_session, "res-none-1", 5)
    payload = models.DownloadBookTaskPayload(book_id=book.id)

    assert _resolve(db_session, book, payload) is None


def test_resolution_all_excluded_returns_empty_list(db_session):
    book, chapters = _make_book_with_chapters(db_session, "res-allexcl-1", 3)
    payload = models.DownloadBookTaskPayload(
        book_id=book.id,
        excluded_chapters=[ch.id for ch in chapters],
    )

    assert _resolve(db_session, book, payload) == []


def test_resolution_excluded_single_chapter(db_session):
    book, chapters = _make_book_with_chapters(db_session, "res-single-1", 4)
    payload = models.DownloadBookTaskPayload(
        book_id=book.id,
        excluded_chapters=[chapters[2].id],
    )

    result = _resolve(db_session, book, payload)
    assert result is not None
    assert chapters[2].id not in result
    assert len(result) == 3


def test_resolution_excluded_does_not_leak_other_book_chapters(db_session):
    """Chapters from a different book must never appear in the result."""
    book_a, ch_a = _make_book_with_chapters(db_session, "res-isol-a", 3)
    book_b, ch_b = _make_book_with_chapters(db_session, "res-isol-b", 3)

    payload = models.DownloadBookTaskPayload(
        book_id=book_a.id,
        excluded_chapters=[ch_a[0].id],
    )

    result = _resolve(db_session, book_a, payload)
    assert result is not None

    sresult = set(result)

    assert ch_a[0].id not in sresult
    assert {ch_a[1].id, ch_a[2].id} <= sresult
    for ch in ch_b:
        assert ch.id not in sresult


def test_resolution_explicit_chapters_takes_precedence_over_excluded(db_session):
    """When both fields are set, explicit chapters wins (task runner ignores excluded)."""
    book, chapters = _make_book_with_chapters(db_session, "res-both-1", 5)
    explicit = [chapters[0].id, chapters[1].id]
    payload = models.DownloadBookTaskPayload(
        book_id=book.id,
        chapters=explicit,
        excluded_chapters=[chapters[2].id],
    )

    assert _resolve(db_session, book, payload) == explicit
