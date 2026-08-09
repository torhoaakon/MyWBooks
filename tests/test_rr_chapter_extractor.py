import pytest
from bs4 import BeautifulSoup

from mywbooks.providers.royalroad import RoyalRoadChapterPageExtractor

CHAPTER_HTML = """
<html>
  <body>
    <div class="fic-header"><h1>Fiction Title</h1></div>
    <div class="chapter-content">
      <h1>Chapter 3 — A Great Reveal</h1>
      <p>First paragraph.</p>
      <p>Second paragraph.</p>
    </div>
  </body>
</html>
"""


def test_chapter_extractor_finds_title_and_content():
    extractor = RoyalRoadChapterPageExtractor()
    soup = BeautifulSoup(CHAPTER_HTML, "lxml")
    page = extractor.extract_chapter(soup)
    assert page is not None
    assert "Fiction Title" in (page.title or "")
    # Content should be a Tag; convert to string for simple assertions
    html = str(page.content)
    assert "<p>First paragraph.</p>" in html


HIDDEN_PARA_HTML = """
<html>
  <head>
    <style>.xAbCdEf { display: none; }</style>
  </head>
  <body>
    <div class="chapter-content">
      <h1>Chapter 1</h1>
      <p>Real content.</p>
      <p class="xAbCdEf">This content is stolen from Royal Road. If you're reading this, it has been stolen. Please support the author.</p>
    </div>
  </body>
</html>
"""

HEADLESS_HTML = """
<html>
  <body>
    <div class="chapter-content">
      <h1>Chapter 1</h1>
      <p>Real content.</p>
      <p class="xAbCdEf">Stolen paragraph — no head tag to parse classes from.</p>
    </div>
  </body>
</html>
"""


def test_hidden_stolen_content_paragraph_is_removed():
    extractor = RoyalRoadChapterPageExtractor()
    soup = BeautifulSoup(HIDDEN_PARA_HTML, "lxml")
    page = extractor.extract_chapter(soup)
    assert page is not None
    html = str(page.content)
    assert "stolen" not in html.lower()
    assert "Real content." in html


def test_hidden_paragraph_not_present_when_no_hidden_classes():
    extractor = RoyalRoadChapterPageExtractor()
    soup = BeautifulSoup(CHAPTER_HTML, "lxml")
    page = extractor.extract_chapter(soup)
    assert page is not None
    assert "First paragraph." in str(page.content)


def test_headless_page_does_not_raise():
    extractor = RoyalRoadChapterPageExtractor()
    soup = BeautifulSoup(HEADLESS_HTML, "lxml")
    page = extractor.extract_chapter(soup)
    assert page is not None
    assert "Real content." in str(page.content)


def test_rr_extractor_strict_error_includes_url():
    from mywbooks.ebook_generator import ExtractOptions
    from mywbooks.providers.royalroad import (
        ChapterParseError,
        RoyalRoadChapterPageExtractor,
    )

    ex = RoyalRoadChapterPageExtractor()
    soup = BeautifulSoup("<html><body>No chapter</body></html>", "lxml")
    with pytest.raises(ChapterParseError) as ei:
        ex.extract_chapter(
            soup, options=ExtractOptions(url="https://rr/chap/42", strict=True)
        )
    assert "Could not locate chapter content" in str(ei.value)
    assert "https://rr/chap/42" in str(ei.value)
