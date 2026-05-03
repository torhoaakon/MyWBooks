import pytest
from mywbooks.book import make_epub_filename


def test_range():
    assert make_epub_filename("My Book", 1, 50) == "My Book - Ch. 1\u201350.epub"


def test_single_chapter():
    assert make_epub_filename("My Book", 7, 7) == "My Book - Ch. 7.epub"


def test_unsafe_chars_replaced():
    name = make_epub_filename('A/B\\C:D*E?"F<G>H|I', 1, 1)
    assert "/" not in name
    assert "\\" not in name
    assert ":" not in name
    assert "*" not in name
    assert "?" not in name
    assert '"' not in name
    assert "<" not in name
    assert ">" not in name
    assert "|" not in name


def test_title_truncated_to_60_chars():
    long_title = "A" * 80
    name = make_epub_filename(long_title, 1, 5)
    title_part = name.split(" - Ch.")[0]
    assert len(title_part) <= 60


def test_empty_title_fallback():
    name = make_epub_filename("", 1, 10)
    assert name.endswith(".epub")
