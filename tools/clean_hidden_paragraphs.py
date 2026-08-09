#!/usr/bin/env python3
"""T-069: retroactively strip hidden anti-piracy paragraphs from already-cached
RoyalRoad chapters, using the on-disk raw HTML cache (no network calls).

Dry run by default — pass --apply to actually write changes to the DB.

Usage:
    uv run python tools/clean_hidden_paragraphs.py [--db-path PATH] [--cache-dir DIR] [--apply] [--limit N]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic_core import Url
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mywbooks.ebook_generator import ExtractOptions
from mywbooks.models import Book, Chapter, ProviderKey
from mywbooks.providers import get_provider_by_key
from mywbooks.providers.royalroad import ChapterParseError
from mywbooks.utils import url_hash


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db-path",
        default=None,
        help="Path to the sqlite DB file (default: whatever mywbooks.db.SessionLocal points at)",
    )
    p.add_argument(
        "--cache-dir",
        default=None,
        help="Path to the raw HTML cache dir (default: $CACHE_DIR or ./cache)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to the DB (default is a dry run that only reports)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N candidate chapters (for testing)",
    )
    return p.parse_args()


def make_session(db_path: str | None) -> Session:
    if db_path is None:
        from mywbooks.db import SessionLocal

        return SessionLocal()
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def main() -> None:
    args = parse_args()

    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(os.getenv("CACHE_DIR", "./cache"))
    if not cache_dir.is_dir():
        raise SystemExit(f"Cache dir not found: {cache_dir}")

    db = make_session(args.db_path)
    prov = get_provider_by_key(ProviderKey.ROYALROAD)

    candidates = (
        db.query(Chapter)
        .join(Book, Chapter.book_id == Book.id)
        .filter(Book.provider == ProviderKey.ROYALROAD, Chapter.is_fetched.is_(True))
        .order_by(Chapter.id)
        .all()
    )

    updated = skipped_no_cache = unchanged = failed = 0
    processed = 0

    for chapter in candidates:
        if args.limit is not None and processed >= args.limit:
            break
        processed += 1

        cache_file = cache_dir / f"{url_hash(Url(chapter.source_url))}.html"
        if not cache_file.is_file():
            skipped_no_cache += 1
            continue

        soup = BeautifulSoup(cache_file.read_bytes(), features="lxml")

        try:
            page = prov.extract_chapter(
                soup,
                options=ExtractOptions(
                    url=chapter.source_url, strict=True, fallback_title=chapter.title
                ),
            )
        except ChapterParseError as e:
            failed += 1
            print(f"FAILED  chapter={chapter.id} url={chapter.source_url}: {e}")
            continue

        if not page or not page.content:
            failed += 1
            print(
                f"FAILED  chapter={chapter.id} url={chapter.source_url}: "
                "extractor returned no content"
            )
            continue

        new_html = str(page.content)
        if new_html == chapter.content_html:
            unchanged += 1
            continue

        updated += 1
        verb = "UPDATE" if args.apply else "WOULD UPDATE"
        print(f"{verb}  chapter={chapter.id} book_id={chapter.book_id} url={chapter.source_url}")
        if args.apply:
            chapter.content_html = new_html

    if args.apply and updated:
        db.commit()
    else:
        db.rollback()

    print()
    print(f"candidates (royalroad, is_fetched):  {len(candidates)}")
    print(f"processed:                           {processed}")
    print(f"updated:                              {updated}" + ("" if args.apply else "  (dry run — rerun with --apply to write)"))
    print(f"unchanged:                            {unchanged}")
    print(f"skipped (no cache file):              {skipped_no_cache}")
    print(f"failed:                               {failed}")


if __name__ == "__main__":
    main()
