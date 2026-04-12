import pytest

# Perhaps export a public wrapper.
from mywbooks.providers.royalroad import FictionParseError, _parse_fiction_page

FICTION_HTML = """
<html>
  <body>
    <div class="fic-header">
      <h1>My RR Fiction</h1>
      <a href="/profile/1234/author-name">Author Name</a>
      <img src="https://royalroadcdn.com/covers/cover123.jpg">
    </div>

    <div class="description">A great story about magic and adventure.</div>

    <span class="tags">
      <a class="fiction-tag">Fantasy</a>
      <a class="fiction-tag">Magic</a>
    </span>

    <div class="chapters">
      <a href="/fiction/5555/my-rr-fiction/chapter/1">Chapter One</a>
      <a href="/fiction/5555/my-rr-fiction/chapter/2">Chapter Two</a>
      <a href="/fiction/5555/my-rr-fiction/chapter/3">Chapter Three</a>
    </div>
  </body>
</html>
"""


def test_parse_fiction_page_case1():
    base = "https://www.royalroad.com/fiction/5555/my-rr-fiction"
    meta, chapter_urls = _parse_fiction_page(
        base, FICTION_HTML, chapter_toc_strategies=2  # Should succeed with =2 (Default)
    )

    assert meta.title == "My RR Fiction"
    assert meta.author == "Author Name"
    assert meta.language in (
        "en",
        "english",
        "en-us",
        "en-gb",
    )  # we default to en if missing
    assert "cover123.jpg" in str(meta.cover_image)

    assert len(chapter_urls) == 3
    urls = [u for u, _title in chapter_urls]
    assert all(u.startswith("https://www.royalroad.com") for u in urls)
    assert "/chapter/1" in urls[0]
    titles = [t for _u, t in chapter_urls]
    assert titles[0] == "Chapter One"

    assert meta.description == "A great story about magic and adventure."
    assert meta.tags == ["Fantasy", "Magic"]


def test_parse_fiction_page_case1_chapters_fail():
    base = "https://www.royalroad.com/fiction/5555/my-rr-fiction"
    with pytest.raises(FictionParseError) as ei:
        _parse_fiction_page(
            base, FICTION_HTML, chapter_toc_strategies=1  # Should fail with =1
        )
    assert "Could not locate chapter table of contents" in str(ei.value)


FICTION_HTML_NO_CANON_TOC = """
<html>
  <body>
    <div class="fic-header">
      <h1>Fallback Fiction</h1>
      <a href="/profile/42/author">Author</a>
    </div>
    <div class="chapter-list">
      <ul>
        <li><a href="/fiction/9999/x/chapter/10">Ch 10</a></li>
        <li><a href="/fiction/9999/x/chapter/11">Ch 11</a></li>
      </ul>
    </div>
  </body>
</html>
"""


def test_parse_fiction_page_case2():
    base = "https://www.royalroad.com/fiction/9999/x"
    meta, links = _parse_fiction_page(
        base, FICTION_HTML_NO_CANON_TOC, strict=True, chapter_toc_strategies=2
    )
    assert meta.title == "Fallback Fiction"
    assert len(links) == 2
    assert links[0][0].endswith("/chapter/10")
    assert links[0][1] == "Ch 10"


def test_parse_fiction_page_case2_chapters_fail():
    base = "https://www.royalroad.com/fiction/9999/x"
    with pytest.raises(FictionParseError) as ei:
        _parse_fiction_page(
            base,
            FICTION_HTML_NO_CANON_TOC,
            strict=True,
            chapter_toc_strategies=1,  # Should fail with =1
        )
    assert "Could not locate chapter table of contents" in str(ei.value)
