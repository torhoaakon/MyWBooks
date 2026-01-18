from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from bs4 import BeautifulSoup
from pydantic_core import Url

from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.download_manager import DownlaodManager


class FakeDownloadManager(DownlaodManager):
    """
    In-memory fake for tests. Supplies bytes from a dict instead of hitting the network.
    Tracks calls to `get_data` so tests can assert caching behavior.
    """

    def __init__(self, base_cache_dir: Path, mapping: Dict[str, bytes]):
        super().__init__(base_cache_dir)
        self._mapping = mapping
        self.calls: Dict[str, int] = {}

    # core primitive for network bytes
    def get_data(
        self,
        url: Url,
        *,
        fileext=None,
        cache_filename: Optional[str] = None,
        ignore_cache=False,
    ):
        surl = str(url)

        self.calls[surl] = self.calls.get(surl, 0) + 1
        if isinstance(url, str):
            u = url
        else:
            u = str(url)
        try:
            return self._mapping[u]
        except KeyError:
            raise AssertionError(f"FakeDownloadManager has no bytes for URL: {u}")

    # keep caching semantics the same, but use our get_data()
    # (delegate to base implementation, which writes/reads cache files)


class FakeAsyncDownloadManager(AsyncDownloadManager):
    """
    In-memory fake for tests. Supplies bytes from a dict instead of hitting the network.
    Tracks calls to `get_data` so tests can assert caching behavior.
    """

    def __init__(self, base_cache_dir: Path, mapping: Dict[str, bytes]):
        super().__init__(base_cache_dir)
        self._mapping = mapping
        self.calls: Dict[str, int] = {}

    # core primitive for network bytes
    async def get_data(
        self,
        url: Url,
        *,
        fileext=None,
        cache_filename: Optional[str] = None,
        ignore_cache=False,
    ):
        surl = str(url)

        self.calls[surl] = self.calls.get(surl, 0) + 1
        if isinstance(url, str):
            u = url
        else:
            u = str(url)
        try:
            return self._mapping[u]
        except KeyError:
            raise AssertionError(f"FakeAsyncDownloadManager has no bytes for URL: {u}")

    # keep caching semantics the same, but use our get_data()
    # (delegate to base implementation, which writes/reads cache files)
