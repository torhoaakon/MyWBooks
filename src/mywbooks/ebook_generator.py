import asyncio
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, NamedTuple, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from ebooklib import epub
from pydantic_core import Url

from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.book import BookConfig, Chapter, Image


@dataclass
class ChapterPageContent:
    title: str | None
    content: "BeautifulSoup | Tag"
    # ? image_urls: list[str]


@dataclass
class ExtractOptions:
    url: str | None = None  # helps error messages / logging
    strict: bool = False  # raise on failure instead of returning None
    fallback_title: str | None = None


class ChapterPageExtractor(ABC):
    @abstractmethod
    def extract_chapter(
        self,
        page_content_bs: BeautifulSoup,
        *,
        options: ExtractOptions | None = None,
    ) -> Optional[ChapterPageContent]:
        """Parse a single chapter page into (title, content)."""

        logging.fatal("Using base {self.class.name}")
        sys.exit(1)

    def __call__(self, page_content_bs: BeautifulSoup) -> Optional[ChapterPageContent]:
        return self.extract_chapter(page_content_bs)


class EbookGeneratorConfig(NamedTuple):
    book_config: BookConfig

    # css_filepath: Path # = KINDLE_CSS_DEFAULT

    include_images: bool = True
    include_chapter_titles: bool = False
    image_resize_max: tuple[int, int] = (1024, 1024)
    epub_css_filepath: str = "assets/kindle.css"

    # TODO: This should probably not include the extension
    epub_cover_image_path: str = "cover.png"
    epub_images_path: str = "images"


class EbookGenerator:
    book_id: str
    config: EbookGeneratorConfig
    chapter_page_exacter: Optional[ChapterPageExtractor]

    ebook: epub.EpubBook

    chapters: list[Chapter]
    # images: dict[str, tuple[str, epub.EpubImage]]
    images_new: dict[str, Image]  #  The keys should be the src_url

    # images_new: ImageMap

    def __init__(
        self,
        book_id: str,
        download_manager: AsyncDownloadManager,
        config: EbookGeneratorConfig,
        chapter_page_exacter: Optional[ChapterPageExtractor] = None,
    ):
        self.book_id = book_id
        self.config = config
        self.chapter_page_exacter = chapter_page_exacter
        self.download_manager: AsyncDownloadManager = download_manager
        self.chapters = []
        self.images_new = {}

    def add_chapter(self, chapter: Chapter) -> None:
        """
        Add a pre-extracted Chapter (from a WebBook). We still:
          - rewrite <img src="..."> to packaged paths,
          - collect images into self.images_new.
        """
        # Parse the chapter's HTML so we can rewrite image src attributes
        bs = BeautifulSoup(chapter.content, features="lxml")

        # Reuse the existing image management to:
        # - dedupe images across chapters
        # - rewrite <img src> → packaged path (e.g., 'images/<id>.jpg')
        ch_images = self.manage_chapter_img_tags(bs)

        # Store the (possibly) rewritten HTML string
        self.chapters.append(
            Chapter(
                title=chapter.title,
                content=str(bs),
                images=ch_images,
                source_url=chapter.source_url,
            )
        )

    def add_chapter_page(
        self,
        page_content: str,
        *,
        chapter_page_exactor: Optional[ChapterPageExtractor] = None,
        src_url: str | None = None,
    ) -> None:
        # NOTE: This ChapterPageContent type is a bit strange

        bs = BeautifulSoup(page_content, features="lxml")

        extractor = chapter_page_exactor or self.chapter_page_exacter
        assert extractor is not None

        extracted_content = extractor(bs)
        assert extracted_content is not None  # TODO: Log error instead

        chpr_images = self.manage_chapter_img_tags(extracted_content.content)

        self.chapters.append(
            Chapter(
                title=extracted_content.title or "No Title",  # TODO: Log no title
                content=str(extracted_content.content),
                images=chpr_images,
                source_url=src_url,
            )
        )

    def manage_chapter_img_tags(
        self, bs: Tag, source_url: Url | None = None
    ) -> dict[str, Image]:
        images: dict[str, Image] = {}

        for img in bs.select("img"):
            src_url = img.get("src")
            # logging.info("Include: ", src_url)

            # We remove image tags with no src
            if src_url is None:
                img.decompose()
                continue

            assert isinstance(src_url, str)

            im = images.get(src_url)
            if not im:
                im = self.images_new.get(src_url)
                if not im:
                    # TODO: full_url = urljoin(source_url, src_url)
                    full_url = src_url

                    im = Image.by_src_url(Url(src_url))
                    self.images_new[src_url] = im

                images[src_url] = im

            img["src"] = im.get_ebook_src(self.config.epub_images_path)
        return images

    # Export the generated Ebook as an epub

    async def export_as_epub(self, local_epub_filepath: Path) -> None:
        ebook = epub.EpubBook()
        # mandatory metadata
        ebook.set_identifier(self.book_id)

        cf = self.config.book_config
        ebook.set_title(cf.title)
        ebook.set_language(cf.language)
        ebook.add_author(cf.author)

        def read_css():
            with open(self.config.epub_css_filepath, "rb") as f:
                return f.read()

        css_content = await asyncio.to_thread(read_css)
        css = epub.EpubItem(
            uid="default",
            file_name=self.config.epub_css_filepath,  # This is a bit strange ?
            media_type="text/css",
            content=css_content,
        )
        ebook.add_item(css)

        # Add cover image
        if cf.cover_image is not None:
            if isinstance(cf.cover_image, Url):
                cover_img_data = await self.download_manager.get_and_cache_image_data(
                    cf.cover_image
                )
            elif isinstance(cf.cover_image, Path):

                def read_cover():
                    with open(cf.cover_image, "rb") as f:
                        return f.read()

                cover_img_data = await asyncio.to_thread(read_cover)
            else:
                assert False, "Unreachable"
            ebook.set_cover(self.config.epub_cover_image_path, cover_img_data)

        # Include the chapters
        chapter_count = 0
        for chtr in self.chapters:
            content = chtr.get_content(
                include_images=self.config.include_images,
                include_chapter_title=self.config.include_chapter_titles,
            )

            # We are counting the added chapters
            chapter_count += 1
            epub_chapter = epub.EpubHtml(
                title=chtr.title, file_name=f"chapter_{chapter_count}.xhtml"
            )
            epub_chapter.set_content("".join(content))

            ebook.add_item(epub_chapter)
            ebook.toc.append(epub_chapter)
            ebook.spine.append(epub_chapter)

        # Include the Images
        img_data_futures: list[Awaitable[bytes | None]] = []
        imgs = []

        for _, im in self.images_new.items():
            imgs.append(im)
            img_data_futures.append(
                im.get_image_data_async(
                    self.download_manager, *self.config.image_resize_max
                )
            )

        image_data_array = await asyncio.gather(*img_data_futures)

        for image_data, im in zip(image_data_array, imgs):
            if not image_data:
                continue

            ebook.add_item(
                epub.EpubImage(
                    uid=im.get_id(),
                    file_name=im.get_ebook_src(self.config.epub_images_path),
                    media_type=im.get_media_type(),
                    content=image_data,
                )
            )

        ebook.add_item(epub.EpubNcx())
        ebook.add_item(epub.EpubNav())

        await asyncio.to_thread(epub.write_epub, local_epub_filepath, ebook)
