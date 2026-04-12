from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from PIL import Image
from pydantic_core import Url

from .fakes import FakeAsyncDownloadManager


def make_jpeg_bytes(w=120, h=80) -> bytes:
    img = Image.new("RGB", (w, h), (128, 160, 192))
    b = BytesIO()
    img.save(b, format="JPEG")
    return b.getvalue()


@pytest.mark.asyncio
async def test_get_and_cache_html_uses_cache(tmp_path: Path):
    html_surl = "https://example.test/page"
    html_url = Url(html_surl)
    html_bytes = b"<html><body><h1>Hi</h1></body></html>"

    fdm = FakeAsyncDownloadManager(tmp_path, {str(html_url): html_bytes})

    # first call should invoke get_data once and write cache
    soup1 = await fdm.get_and_cache_html(html_url, ignore_cache=False)
    assert isinstance(soup1, BeautifulSoup)
    assert fdm.calls[html_surl] == 1

    # second call should be served from cache (no new get_data call)
    soup2 = await fdm.get_and_cache_html(html_url, ignore_cache=False)
    assert fdm.calls[html_surl] == 1  # still 1


@pytest.mark.asyncio
async def test_get_and_cache_image_resizes_and_caches(tmp_path: Path):
    img_url_str = "https://example.test/image.jpg"
    img_url = Url(img_url_str)
    img_bytes = make_jpeg_bytes(2000, 1500)

    fdm = FakeAsyncDownloadManager(tmp_path, {img_url_str: img_bytes})
    out1 = await fdm.get_and_cache_image_data(
        img_url, ignore_cache=False, max_width=256, max_height=256
    )
    assert isinstance(out1, (bytes, bytearray))
    # second call should come from cache (no additional get_data)
    out2 = await fdm.get_and_cache_image_data(
        img_url, ignore_cache=False, max_width=256, max_height=256
    )
    assert fdm.calls[img_url_str] == 1
    # cached file exists
    cache_files = list(tmp_path.glob("*.jpg"))
    assert cache_files, "resized image should be saved in cache"
