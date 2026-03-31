import asyncio
import io
import logging
import os
from collections.abc import Buffer
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from PIL import Image
from pydantic_core import Url

from mywbooks.utils import url_hash


class AsyncDownloadManager:
    hdrs = {"User-Agent": "Mozilla/5.0"}

    def __init__(
        self,
        base_cache_dir: Optional[Path] = None,
        *,
        default_cooldown: float = 0.5,
        max_concurrent_per_host: int = 3,
    ) -> None:
        if base_cache_dir:
            self.base_cache_dir = base_cache_dir
        else:
            self.base_cache_dir = Path(os.getenv("CACHE_DIR", "./cache"))

        self.base_cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            headers=self.hdrs, timeout=30.0, follow_redirects=True
        )
        self.default_cooldown = default_cooldown
        self.max_concurrent_per_host = max_concurrent_per_host
        self.domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self.domain_last_request: dict[str, float] = {}
        self.domain_timestamp_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "AsyncDownloadManager":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    def get_url_hash(self, url: Url) -> str:
        return url_hash(url)

    def get_cache_filename(self, url: Url, fileext: Optional[str] = None) -> str:
        hash_filename = self.get_url_hash(url)
        if fileext is not None:
            hash_filename += fileext
        return hash_filename

    def is_valid_cache(self, cache_filename: str) -> bool:
        cache_filepath = self.base_cache_dir / cache_filename
        return cache_filepath.exists()

    async def read_valid_cache_file(self, cache_filename: str) -> bytes:
        def read_file():
            with open(self.base_cache_dir / cache_filename, "rb") as f:
                return f.read()

        return await asyncio.to_thread(read_file)

    async def write_to_cache_file(self, content: Buffer, cache_filename: str) -> int:
        def write_file():
            with open(self.base_cache_dir / cache_filename, "wb") as f:
                return f.write(content)

        return await asyncio.to_thread(write_file)

    async def get_data(
        self,
        url: Url,
        *,
        fileext: Optional[str] = None,
        cache_filename: Optional[str] = None,
        ignore_cache: bool = False,
    ) -> bytes:
        if not ignore_cache:
            if cache_filename is None:
                cache_filename = self.get_cache_filename(url, fileext)
            if self.is_valid_cache(cache_filename):
                return await self.read_valid_cache_file(cache_filename)

        host = url.host or "unknown"
        
        if host not in self.domain_semaphores:
            self.domain_semaphores[host] = asyncio.Semaphore(self.max_concurrent_per_host)
            self.domain_timestamp_locks[host] = asyncio.Lock()
        
        sem = self.domain_semaphores[host]
        ts_lock = self.domain_timestamp_locks[host]

        async with sem:
            # Spacing out the start times
            async with ts_lock:
                now = asyncio.get_event_loop().time()
                last_time = self.domain_last_request.get(host, 0)
                wait_time = max(0, self.default_cooldown - (now - last_time))
                
                # Advance the last_request time as if we started after the wait
                # This ensures the next task in line waits for its own cooldown
                self.domain_last_request[host] = now + wait_time
            
            if wait_time > 0:
                # logging.info(f"Rate limiting {host}: waiting {wait_time:.2f}s to start")
                await asyncio.sleep(wait_time)

            import sys

            sys.stderr.write(f"INFO: Downloading '{url}'\n")

            max_retries = 5
            base_delay = 2.0
            for attempt in range(max_retries):
                response = await self.client.get(str(url))
                
                if response.status_code == 429:
                    delay = base_delay * (2**attempt)
                    sys.stderr.write(
                        f"WARN: 429 Too Many Requests for {host}. Retrying in {delay}s (attempt {attempt + 1}/{max_retries})\n"
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.content

            raise httpx.HTTPStatusError(
                "Max retries exceeded for 429",
                request=response.request,
                response=response,
            )

    async def get_and_cache_data(
        self,
        url: Url,
        *,
        fileext: Optional[str] = None,
        cache_filename: Optional[str] = None,
        ignore_cache: bool = False,
    ) -> bytes:
        if cache_filename is None:
            cache_filename = self.get_cache_filename(url, fileext)

        if not ignore_cache and self.is_valid_cache(cache_filename):
            return await self.read_valid_cache_file(cache_filename)

        content = await self.get_data(url, fileext=fileext, ignore_cache=True)
        await self.write_to_cache_file(content, cache_filename)
        return content

    async def get_html(self, url: Url, *, ignore_cache: bool = False) -> BeautifulSoup:
        content = await self.get_data(url, fileext=".html", ignore_cache=ignore_cache)
        return BeautifulSoup(content, features="lxml")

    async def get_and_cache_html(
        self, url: Url, *, ignore_cache: bool = False
    ) -> BeautifulSoup:
        content = await self.get_and_cache_data(
            url, fileext=".html", ignore_cache=ignore_cache
        )
        return BeautifulSoup(content, features="lxml")

    async def get_and_cache_image_data(
        self,
        url: Url,
        *,
        ignore_cache: bool = False,
        max_width: int = 8096,
        max_height: int = 8096,
    ) -> bytes:
        cache_filename = "%s_%u_%u.jpg" % (
            self.get_cache_filename(url, fileext=""),
            max_width,
            max_height,
        )

        if not ignore_cache and self.is_valid_cache(cache_filename):
            return await self.read_valid_cache_file(cache_filename)

        content = await self.get_and_cache_data(
            url, fileext=None, ignore_cache=ignore_cache
        )

        def process_image(img_content: bytes) -> bytes:
            content_io = io.BytesIO(img_content)
            im = Image.open(content_io, "r").convert("RGB")
            im.thumbnail((max_width, max_height))
            out_io = io.BytesIO()
            im.save(out_io, format="JPEG")
            return out_io.getvalue()

        resized_content = await asyncio.to_thread(process_image, content)

        await self.write_to_cache_file(resized_content, cache_filename)
        return resized_content
