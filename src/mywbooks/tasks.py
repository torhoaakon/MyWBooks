# Example factory that returns the right WebBook subclass from a DB Book row
from pathlib import Path
from typing import Any

import dramatiq
from pydantic_core import Url

from mywbooks import models
from mywbooks.book import DEFAULT_COVER_URL, EPUB_DIR, BookConfig
from mywbooks.ebook_generator import EbookGeneratorConfig
from mywbooks.task_cleanup import register_cleanup

from . import queue  # This import is IMPORTANT
from .db import SessionLocal
from .download_manager import DownlaodManager
from .models import (
    Book,
    DownloadBookTaskPayload,
    Task,
    TaskStatus,
    TaskType,
    is_download_book_task_payload,
)
from .services.book_ops import export_book_to_epub_from_db, upsert_fiction_toc
from .utils import utcnow


@dramatiq.actor(max_retries=1)
def download_book_task(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.get(models.Task, task_id)
        if not task:
            return  # nothing to do

        task.status = TaskStatus.RUNNING
        task.started_at = utcnow()
        task.attempts += 1
        db.commit()

        # TODO: DEBUG MODE ONLY
        if not task.payload or not is_download_book_task_payload(task.payload):
            raise RuntimeError("Invalid task payload")

        payload = task.payload
        book_id = payload["book_id"]

        book = db.get(Book, book_id)
        if not book:
            raise RuntimeError(f"Book {book_id} not found")

        dm = DownlaodManager(Path("./cache"))
        out_dir = EPUB_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"book-{book.id}-task-{task.id}.epub"

        # NOTE: Should probably not be doing this her here
        upsert_fiction_toc(
            db, book, dm
        )  ## Look for new chapters and changes to metadata

        cover_url: Url = Url(book.cover_url) if book.cover_url else DEFAULT_COVER_URL
        bcfg = BookConfig(
            title=payload.get("title") or book.title,
            author=payload.get("author") or book.author or "",
            language=payload.get("language") or book.language or "en",
            cover_image=(
                Url(payload.get("cover_img") or "")
                if payload.get("cover_img")
                else cover_url
            ),
        )

        keys = [
            "include_images",
            "include_chapter_titles",
            "image_resize_max",
            "epub_css_filepath",
        ]
        cfg = EbookGeneratorConfig(
            book_config=bcfg,
            **{k: payload[k] for k in keys if k in payload and payload[k] is not None},
        )

        export_book_to_epub_from_db(
            db,
            book,
            dm=dm,
            cfg=cfg,
            chapter_list=payload.get("chapters") or None,
            out_path=out_path,
        )

        # Mark success (you could store a file path in payload)
        task.status = TaskStatus.SUCCEEDED
        task.payload["output_path"] = str(out_path)
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
