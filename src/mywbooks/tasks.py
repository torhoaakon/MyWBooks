# Example factory that returns the right WebBook subclass from a DB Book row
import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Concatenate, cast

from arq.connections import ArqRedis
from pydantic_core import Url
from sqlalchemy.orm import Session

from .async_download_manager import AsyncDownloadManager
from .book import DEFAULT_COVER_URL, EPUB_DIR, BookConfig
from .db import SessionLocal
from .ebook_generator import EbookGeneratorConfig
from .email_sender import send_ebook_email
from .models import (
    Book,
    DownloadBookTaskPayload,
    SendBookTaskPayload,
    Task,
    TaskStatus,
    TaskType,
)
from .services.book_ops import export_book_to_epub_from_db, upsert_fiction_toc
from .task_cleanup import register_cleanup
from .utils import utcnow

REGISTERED_TASK_FUNCTIONS = []


async def schedule_task(
    db: Session,
    arq_pool: ArqRedis,
    type: TaskType,
    user_id: int | None,
    payload: dict[str, Any],
) -> Task:

    # Create a Task row
    task = Task(
        type=type,
        status=TaskStatus.QUEUED,
        user_id=user_id,
        payload=payload,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    match type:
        case TaskType.DOWNLOAD_BOOK:
            await arq_pool.enqueue_job("download_book_task", task.id)
        case TaskType.SEND_BOOK:
            await arq_pool.enqueue_job("send_book_task", task.id)
        case _:
            raise RuntimeError("Unreachable")

    return task


CtxType = dict[Any, Any]
FuncType = Callable[Concatenate[CtxType, Session, Task, ...], Awaitable[None]]


def register_task(func: FuncType):
    async def try_execute_task(ctx, task_id: int):
        # NOTE: We are using a sync DB session in an async context.
        # ideally we would run this in a thread executor or use AsyncSession.
        # For now, we assume these are fast enough.
        db = SessionLocal()

        try:
            task = db.get(Task, task_id)

            if not task:
                return  # nothing to do

            task.status = TaskStatus.RUNNING
            task.started_at = utcnow()
            task.attempts += 1
            db.commit()

            # Execute the task
            await func(ctx, db, task)

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
            raise  # let arq retry (if configured) or handle failure
        finally:
            db.close()

    try_execute_task.__name__ = func.__name__
    REGISTERED_TASK_FUNCTIONS.append(try_execute_task)

    return try_execute_task


# ####
# ## Helpers
# ####


def get_dm(ctx: CtxType) -> AsyncDownloadManager:
    return cast(AsyncDownloadManager, ctx["dm"])


# ####
# ##  Task functions
# ####


@register_task
async def download_book_task(ctx: CtxType, db: Session, task: Task) -> None:
    payload = DownloadBookTaskPayload.model_validate(task.payload)

    book = db.get(Book, payload.book_id)
    if not book:
        raise RuntimeError(f"Book {payload.book_id} not found")

    out_dir = EPUB_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"book-{book.id}-task-{task.id}.epub"

    dm = get_dm(ctx)

    # NOTE: logic that should be async or threaded eventually
    await upsert_fiction_toc(db, book, dm)

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

    await export_book_to_epub_from_db(
        db,
        book,
        dm=dm,
        cfg=EbookGeneratorConfig(book_config=bcfg, **overrides),
        chapter_list=payload.chapters or None,
        out_path=out_path,
    )

    if payload.send_by_email:
        payload.send_by_email.book_path = str(out_path)

        arq_pool: ArqRedis = ctx["redis"]
        await schedule_task(
            db,
            arq_pool,
            TaskType.SEND_BOOK,
            task.user_id,
            payload.send_by_email.model_dump(),
        )

    # We update the task payload, the db is committed at the end of the task
    task.payload = payload.model_dump()


@register_task
async def send_book_task(ctx: CtxType, db: Session, task: Task) -> None:
    payload = SendBookTaskPayload.model_validate(task.payload)

    # Send Email (blocking I/O, run in thread)
    await asyncio.to_thread(
        send_ebook_email,
        recipient_email=payload.recipient_email,
        ebook_path=Path(payload.book_path),
        book_title=payload.book_title,
    )


# ####
# ## Cleanup Handlers
# ####


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
