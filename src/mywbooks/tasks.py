# Example factory that returns the right WebBook subclass from a DB Book row
import asyncio
import functools
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Concatenate

from arq.connections import ArqRedis
from pydantic_core import Url
from sqlalchemy import select as _select
from sqlalchemy import update as _update
from sqlalchemy.orm import Session

from mywbooks.book import DEFAULT_COVER_URL, EPUB_DIR, BookConfig, make_epub_filename
from mywbooks.ebook_generator import EbookGeneratorConfig, ExtractOptions
from mywbooks.email_sender import send_ebook_email
from mywbooks.task_cleanup import register_cleanup

from .async_download_manager import AsyncDownloadManager
from .db import SessionLocal
from .models import (
    Book,
    Chapter,
    DownloadBookTaskPayload,
    SendBookTaskPayload,
    Task,
    TaskStatus,
    TaskType,
)
from .providers import get_provider_by_key
from .services.book_ops import (
    ensure_chapter_content,
    export_book_to_epub_from_db,
    upsert_fiction_toc,
)
from .utils import utcnow

logger = logging.getLogger(__name__)

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


# ####
# ## Wrappers
# ####

CtxType = dict[Any, Any]


def register_task(
    func: Callable[Concatenate[CtxType, Session, ...], Awaitable[None]],
) -> Callable[Concatenate[CtxType, ...], Awaitable[None]]:

    @functools.wraps(func)
    async def inner(ctx, *args, **kwargs):
        # NOTE: We are using a sync DB session in an async context.
        # ideally we would run this in a thread executor or use AsyncSession.
        # For now, we assume these are fast enough.
        db = SessionLocal()
        try:
            await func(ctx, db, *args, **kwargs)

        finally:
            db.close()

    REGISTERED_TASK_FUNCTIONS.append(inner)

    return inner


def register_task_with_status(
    func: Callable[Concatenate[CtxType, Session, Task, ...], Awaitable[None]],
) -> Callable[Concatenate[CtxType, Session, ...], Awaitable[None]]:

    @register_task
    @functools.wraps(func)
    async def inner(ctx: CtxType, db: Session, task_id: int, *args, **kwargs):
        try:
            task = db.get(Task, task_id)

            if not task:
                return  # nothing to do

            task.status = TaskStatus.RUNNING
            task.started_at = utcnow()
            task.attempts += 1
            db.commit()

            await func(ctx, db, task, *args, **kwargs)

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
            raise e  # let arq retry (if configured) or handle failure

    return inner


# ####
# ##  Task functions
# ####


@register_task
async def fetch_chapter_task(
    ctx: CtxType, db: Session, chapter_id: int, *args, **kw
) -> None:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        return  # Maybe deleted?

    if chapter.is_fetched:
        return  # Already done

    book = db.get(Book, chapter.book_id)
    if not book:
        return

    dm: AsyncDownloadManager = ctx["dm"]
    prov = get_provider_by_key(book.provider)

    # Download & Cache (Async I/O)
    soup = await dm.get_and_cache_html(Url(chapter.source_url))

    # Extract (Blocking CPU - should be in thread ideally, but small enough for now)
    page = prov.extract_chapter(
        soup,
        options=ExtractOptions(
            url=chapter.source_url, strict=True, fallback_title=chapter.title
        ),
    )

    if page and page.content:
        chapter.title = page.title or chapter.title
        chapter.content_html = str(page.content)
        chapter.fetched_at = utcnow()
        chapter.is_fetched = True
        db.commit()


@register_task_with_status
async def download_book_task(ctx: CtxType, db: Session, task: Task) -> None:
    payload = DownloadBookTaskPayload.model_validate(task.payload)

    book = db.get(Book, payload.book_id)

    if not book:
        raise RuntimeError(f"Book {payload.book_id} not found")

    dm: AsyncDownloadManager = ctx["dm"]
    out_dir = EPUB_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve chapter indices to build a meaningful filename
    if payload.chapters:
        indices = db.execute(
            _select(Chapter.index).where(Chapter.id.in_(payload.chapters))
        ).scalars().all()
    else:
        indices = db.execute(
            _select(Chapter.index).where(Chapter.book_id == book.id)
        ).scalars().all()
    first_idx = min(indices) + 1 if indices else 1
    last_idx = max(indices) + 1 if indices else 1

    book_title = payload.title or book.title or f"book-{book.id}"
    epub_name = make_epub_filename(book_title, first_idx, last_idx)
    out_path = out_dir / epub_name

    # 1. Update TOC (best-effort — do not abort download if source is unreachable)
    try:
        await upsert_fiction_toc(db, book, dm)
    except Exception as exc:
        logger.warning(
            "TOC refresh failed for book %s, proceeding with cached data: %s",
            book.id,
            exc,
        )

    # 2. Identify missing chapters
    arq_pool = ctx["redis"]
    await ensure_chapter_content(
        db,
        book.id,
        arq_pool,
        chapters_by_id=payload.chapters,
        max_retries=30,
        check_completion_sleep_delay=5,
    )

    # 3. Generate EPUB
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

    # Pass dm=dm to allow image downloads during generation if needed
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
        payload.send_by_email.chapter_ids = payload.chapters  # for delivered_at tracking

        await schedule_task(
            db,
            arq_pool,
            TaskType.SEND_BOOK,
            task.user_id,
            payload.send_by_email.model_dump(),
        )

    payload.output_path = str(out_path)
    task.payload = payload.model_dump()


@register_task_with_status
async def send_book_task(ctx: CtxType, db: Session, task: Task) -> None:
    payload = SendBookTaskPayload.model_validate(task.payload)

    # Send Email (Async)
    await send_ebook_email(
        recipient_email=payload.recipient_email,
        ebook_path=Path(payload.book_path),
        book_title=payload.book_title,
    )

    # Mark chapters as delivered
    if payload.chapter_ids:
        db.execute(
            _update(Chapter)
            .where(Chapter.id.in_(payload.chapter_ids))
            .values(delivered_at=utcnow())
        )
        db.commit()


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
