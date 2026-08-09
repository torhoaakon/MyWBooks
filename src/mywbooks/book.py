import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic_core import Url

from . import models
from .async_download_manager import AsyncDownloadManager
from .utils import url_hash

DEFAULT_COVER_URL = Url("https://www.royalroad.com/favicon.ico")
EPUB_DIR = Path(os.getenv("EPUB_DIR", "var/epubs")).absolute()

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def make_epub_filename(title: str, first_index: int, last_index: int) -> str:
    """Return a safe EPUB filename derived from the book title and chapter range.

    Format: "{title} - Ch. {first}.epub"  (single chapter)
            "{title} - Ch. {first}–{last}.epub"  (range)
    """
    safe_title = _UNSAFE_CHARS.sub("_", title).strip()[:60].rstrip()
    if first_index == last_index:
        chapter_part = f"Ch. {first_index}"
    else:
        chapter_part = f"Ch. {first_index}\u2013{last_index}"
    return f"{safe_title} - {chapter_part}.epub"


ImageID = str


@dataclass(init=True)
class Image:
    url: Url
    url_hash: ImageID
    # image_hash ? TODO: It would be cool to hash the image itself
    image_data: Optional[bytes] = None

    @staticmethod
    def by_src_url(src_url: Url) -> "Image":
        return Image(url=src_url, url_hash=url_hash(src_url))

    async def get_image_data_async(
        self, dm: AsyncDownloadManager, max_width: int = 1024, max_height: int = 1024
    ) -> bytes | None:
        if self.image_data is not None:
            return self.image_data

        try:
            self.image_data = await dm.get_and_cache_image_data(
                self.url, max_width=max_width, max_height=max_height
            )
            return self.image_data
        except Exception as e:
            logging.error(str(e))

            # TODO: Report this in the download status.
            return None

    def get_image_data(
        self, dm: AsyncDownloadManager, max_width: int = 1024, max_height: int = 1024
    ) -> bytes | None:
        # NOTE: This sync method is now deprecated or should be carefully used
        # We might want to remove it later or keep it for non-async parts.
        if self.image_data is not None:
            return self.image_data

        # It's hard to call async from sync.
        # For now, let's just make it return None or raise if we are in sync land
        # but the manager is async.
        # Actually, let's just use asyncio.run if absolutely needed, but better to use the async version.
        return None

    def get_id(self) -> ImageID:
        return self.url_hash

    def get_extension(self) -> str:
        return "jpg"

    def get_media_type(self) -> str:
        return "image/jpg"

    def get_ebook_src(self, base_images_path: str) -> str:
        return f"{base_images_path}/{self.get_id()}.{self.get_extension()}"


@dataclass(frozen=True)
class ChapterRef:
    """Lightweight reference to a chapter that may not be downloaded yet."""

    id: str  # provider chapter id, e.g. "royalroad:21220:ch-123"
    url: Url  # absolute chapter URL
    title: Optional[str] = None


class Chapter(NamedTuple):
    title: str
    content: str
    images: dict[ImageID, Image]

    source_url: Optional[str]

    def get_content(self, include_images: bool, include_chapter_title: bool) -> str:
        content: list[str] = []

        if include_chapter_title:
            content.append("<h1>%s</h1>" % self.title)

        content.append(
            str(self.content)
            if include_images
            else re.sub(r"<img.*>", "", self.content)
        )

        return "".join(content)

    @classmethod
    def from_model(cls, model: models.Chapter) -> "Chapter":
        if not model.is_fetched:
            raise RuntimeError("Trying to turn unfetched chapter model into Chapter")

        html = model.content_html or ""
        bs = BeautifulSoup(html, features="lxml")

        images = {}
        for tag in bs.select("img[src]"):
            src = str(tag["src"]).strip()
            full: Url = Url(
                src if src.startswith("http") else urljoin(model.source_url, src)
            )
            images[url_hash(full)] = Image.by_src_url(full)

        return Chapter(
            title=model.title,
            content=html,
            images=images,
            source_url=model.source_url,
        )


@dataclass
class BookConfig:
    title: str
    language: str
    author: str
    cover_image: Url | Path  # Maybe this should be image type
    description: str | None = None
    tags: list[str] | None = None

    @staticmethod
    def from_model(book: models.Book) -> "BookConfig":
        cover_url: Url = Url(book.cover_url) if book.cover_url else DEFAULT_COVER_URL

        return BookConfig(
            title=book.title,
            author=book.author or "",
            language=book.language,
            cover_image=cover_url,  # adapt if you store Url vs str
        )


@dataclass
class BookData:
    """Aggregated extracted data (format agnostic)."""

    config: BookConfig
    chapters: list[Chapter] = field(default_factory=list)
    images: dict[ImageID, Image] = field(default_factory=dict)

    def add_chapter(self, chapter: Chapter) -> None:
        self.images.update(chapter.images)
        self.chapters.append(chapter)


# Export-time options (can be per-export)
@dataclass
class ExportOptions:
    include_images: bool = True
    include_chapter_titles: bool = True
    image_resize_max: tuple[int, int] = (1024, 1024)
