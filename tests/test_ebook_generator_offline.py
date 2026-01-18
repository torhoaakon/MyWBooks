from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from pydantic_core import Url

from mywbooks.book import BookConfig, Chapter
from mywbooks.ebook_generator import EbookGenerator, EbookGeneratorConfig
from mywbooks.providers.royalroad import RoyalRoadChapterPageExtractor
from tests.fakes import FakeDownloadManager


def make_jpeg_bytes(w=120, h=80) -> bytes:
    img = Image.new("RGB", (w, h), (200, 100, 50))
    b = BytesIO()
    img.save(b, format="JPEG")
    return b.getvalue()


def test_ebook_generator_exports_epub_offline(tmp_path: Path):
    # Prepare CSS file expected by generator
    css_path = tmp_path / "kindle.css"
    css_path.write_text("body { font-family: serif; }", encoding="utf-8")

    # Fake download bytes for cover and an in-chapter image
    cover_url = "https://example.test/cover.jpg"
    img1_url = "https://example.test/img1.jpg"
    fdm = FakeDownloadManager(
        tmp_path,
        {
            cover_url: make_jpeg_bytes(400, 600),
            img1_url: make_jpeg_bytes(320, 240),
        },
    )

    # Book config: use Url for cover so generator goes through DownloadManager
    bc = BookConfig(
        title="Offline Smoke",
        language="en",
        author="Tester",
        cover_image=Url(cover_url),
    )
    cfg = EbookGeneratorConfig(
        book_config=bc,
        epub_css_filepath=str(css_path),
        include_images=True,
        include_chapter_titles=True,
    )

    gen = EbookGenerator(
        book_id="offline-smoke",
        chapter_page_exacter=RoyalRoadChapterPageExtractor(),
        download_manager=fdm,
        config=cfg,
    )

    # A chapter HTML with an image; generator will rewrite <img src> and cache bytes
    chapter_html = f"""
    <div id="chapter-inner">
      <h1>Ch 1</h1>
      <p>Hello world</p>
      <img src="{img1_url}">
    </div>
    """
    gen.add_chapter_page(chapter_html, src_url="https://example.test/ch1")

    out = tmp_path / "out.epub"
    gen.export_as_epub(out)

    # Preview the epub
    # system(f"zathura '{out}'")

    assert out.exists() and out.stat().st_size > 0
