from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from arq import ArqRedis
from pydantic_core import Url
from sqlalchemy.orm import Session

from mywbooks import models
from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.book import Chapter as ChapterDTO
from mywbooks.ebook_generator import (
    EbookGenerator,
    EbookGeneratorConfig,
    ExtractOptions,
)
from mywbooks.providers import get_provider_by_key
from mywbooks.providers.base import Fiction
from mywbooks.utils import utcnow

from .ingest import _upsert_book_meta  # uses list_chapter_refs()
from .ingest import _upsert_chapter_index_from_refs


def provider_for(book: models.Book) -> str:
    return book.provider.value


async def upsert_fiction_toc(
    db: Session,
    book: models.Book,
    dm: AsyncDownloadManager,
    *,
    do_inserts: bool = False,
) -> int:
    """
    Updates Chapter rows for this book (provider-specific ToC discovery).
    Returns number of chapter refs discovered (not inserted count).
    """
    prov = get_provider_by_key(book.provider)
    fic: Fiction = await prov.discover_fiction(dm, Url(book.source_url))

    _upsert_book_meta(db, prov, fic.meta, book=book, do_inserts=do_inserts)
    _upsert_chapter_index_from_refs(db, prov, fic.chapter_refs, book.id)
    return len(fic.chapter_refs)


async def ensure_chapter_content(
    db: Session,
    book_id: int,
    arq_pool: ArqRedis,
    *,
    chapters_by_id: list[int] | None,  # If None, fetch all missing
    max_retries: int = 360,
    check_completion_sleep_delay=5,
) -> int:
    """
    Fill missing content for chapters of a book.
    Returns number of chapters fetched.
    """

    # 1. Identify missing chapters
    q_missing = db.query(models.Chapter).filter(
        models.Chapter.book_id == book_id,
        models.Chapter.is_fetched == False,  # noqa: E712
    )
    if chapters_by_id:
        q_missing = q_missing.filter(models.Chapter.index.in_(chapters_by_id))

    missing_chapters = q_missing.all()
    missing_ids = [ch.id for ch in missing_chapters]

    # 2. Schedule fetch tasks for missing chapters (with deduplication)
    for ch_id in missing_ids:
        await arq_pool.enqueue_job(
            "fetch_chapter_task",
            ch_id,
            _job_id=f"fetch_chapter:{ch_id}",
            _keep_result=3600,  # Keep result for 1 hour
        )

    # 3. Wait for all chapters to be fetched (Polling loop)
    # We poll the DB to check `is_fetched` status.
    # Timeout after: max_retries * 5s = 30 minutes (by default)
    for _ in range(max_retries):
        pending_count = db.query(models.Chapter).filter(
            models.Chapter.book_id == book_id,
            models.Chapter.is_fetched == False,  # noqa: E712
        )
        if chapters_by_id:
            pending_count = pending_count.filter(models.Chapter.id.in_(chapters_by_id))

        count = pending_count.count()
        if count == 0:
            break

        await asyncio.sleep(check_completion_sleep_delay)
    else:
        raise RuntimeError("Timed out waiting for chapters to download")

    return count


async def export_book_to_epub_from_db(
    db: Session,
    book: models.Book,
    cfg: EbookGeneratorConfig,
    out_path: Path,
    # List of chapter IDs to include; if None, include all
    chapter_list: list[int] | None = None,
    *,
    dm: AsyncDownloadManager,
    **kw: dict[str, Any],
) -> Path:
    """
    Build an EPUB purely from DB rows (Book + fetched Chapters).
    If some chapters aren’t fetched yet, they will be skipped.
    """

    # Prepare generator config from DB-only metadata
    gen = EbookGenerator(
        book_id=f"book-{book.id}",
        download_manager=dm,
        config=cfg,
    )

    # Stream chapters from DB, in order
    q_chapters = (
        db.query(models.Chapter)
        .filter(
            models.Chapter.book_id == book.id, models.Chapter.is_fetched == True
        )  # noqa: E712
        .order_by(models.Chapter.index.asc())
    )
    if chapter_list is not None:
        q_chapters = q_chapters.filter(models.Chapter.id.in_(chapter_list))

    rows = q_chapters.all()
    for chm in rows:
        dto = ChapterDTO.from_model(
            chm
        )  # builds a Chapter DTO with images map, etc. :contentReference[oaicite:1]{index=1}
        gen.add_chapter(dto)

    await gen.export_as_epub(out_path)
    return out_path
