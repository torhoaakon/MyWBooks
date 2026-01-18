# Example factory that returns the right WebBook subclass from a DB Book row
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import dramatiq
from pydantic_core import Url
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.book import DEFAULT_COVER_URL, EPUB_DIR, BookConfig
from mywbooks.ebook_generator import EbookGeneratorConfig
from mywbooks.email_sender import send_ebook_email
from mywbooks.task_cleanup import register_cleanup

from . import queue  # This import is IMPORTANT
from .db import SessionLocal
from .download_manager import DownlaodManager
from .models import (
    Book,
    DownloadBookTaskPayload,
    SendBookTaskPayload,
    Task,
    TaskStatus,
    TaskType,
)
from .services.book_ops import export_book_to_epub_from_db, upsert_fiction_toc
from .utils import utcnow


def scedule_task(db: Session, type: TaskType, user_id, payload: dict[str, Any]) -> Task:

    # Create a Task row
    task = models.Task(
        type=type,
        status=models.TaskStatus.QUEUED,
        user_id=user_id,
        payload=payload,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    match type:
        case TaskType.DOWNLOAD_BOOK:
            download_book_task.send(task.id)
        case TaskType.SEND_BOOK:
            send_book_task.send(task.id)
        case _:
            raise RuntimeError("Unreachable")

    return task


@contextmanager
def try_execute_task(task_id: int):
    db = SessionLocal()

    try:
        task = db.get(models.Task, task_id)

        if not task:
            yield (db, task)
            return  # nothing to do

        task.status = TaskStatus.RUNNING
        task.started_at = utcnow()
        task.attempts += 1
        db.commit()

        yield (db, task)

        task.status = TaskStatus.SUCCEEDED
        task.finished_at = utcnow()
        db.commit()

    except Exception as e:
        task = db.get(Task, task_id)  # re-read in case of rollback
        if task:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.finished_at = utcnow()
            db.commit()
        raise  # let Dramatiq retry
    finally:
        db.close()

    raise RuntimeError("Unreachable")


@dramatiq.actor(max_retries=1)
def download_book_task(task_id: int) -> None:
    with try_execute_task(task_id) as (db, task):
        if task is None:
            return

        payload = DownloadBookTaskPayload.model_validate(task.payload)

        book = db.get(Book, payload.book_id)
        if not book:
            raise RuntimeError(f"Book {payload.book_id} not found")

        dm = DownlaodManager(Path("./cache"))
        out_dir = EPUB_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"book-{book.id}-task-{task.id}.epub"

        # NOTE: Should probably not be doing this her here
        upsert_fiction_toc(
            db, book, dm
        )  ## Look for new chapters and changes to metadata

        bcfg = BookConfig(
            title=payload.title or book.title,
            author=payload.author or book.author or "",
            language=payload.language or book.language or "en",
            cover_image=payload.cover_img
            or (Url(book.cover_url) if book.cover_url else None)
            or DEFAULT_COVER_URL,
        )

        overrides = {
            k: getattr(payload, k)
            for k in [
                "include_images",
                "include_chapter_titles",
                "image_resize_max",
                "epub_css_filepath",
            ]
            if getattr(payload, k) is not None
        }

        export_book_to_epub_from_db(
            db,
            book,
            dm=dm,
            cfg=EbookGeneratorConfig(book_config=bcfg, **overrides),
            chapter_list=payload.chapters or None,
            out_path=out_path,
        )

        if payload.send_by_email:
            payload.send_by_email.book_path = out_path
            scedule_task(
                db, TaskType.SEND_BOOK, task.user_id, payload.send_by_email.model_dump()
            )


@dramatiq.actor(max_retries=1)
def send_book_task(task_id: int) -> None:
    with try_execute_task(task_id) as (_, task):
        if task is None:
            return

        payload = SendBookTaskPayload.model_validate(task.payload)

        # Send Email
        send_ebook_email(
            recipient_email=payload.recipient_email,
            ebook_path=payload.book_path,
            book_title=payload.book_title,
        )


@register_cleanup(TaskType.DOWNLOAD_BOOK)  # type: ignore
def cleanup_download_book(task: Task) -> None:
    payload = task.payload or {}
    output_path = payload.get("output_path")
    if not output_path:
        return
    path = Path(output_path).resolve()
    try:
        path.relative_to(EPUB_DIR)
    except ValueError:
        return
    if path.exists():
        path.unlink()


@register_cleanup(TaskType.SEND_BOOK)  # type: ignore
def cleanup_send_book(task: Task) -> None:
    payload = task.payload or {}
    output_path = payload.get("output_path")
    if not output_path:
        return
    path = Path(output_path).resolve()
    try:
        path.relative_to(EPUB_DIR)
    except ValueError:
        return
    if path.exists():
        path.unlink()
