from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NamedTuple, Optional

from bs4 import BeautifulSoup
from pydantic_core import Url

from mywbooks.book import BookConfig, ChapterRef
from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.ebook_generator import ChapterPageContent, ExtractOptions


class InvalidProviderError(Exception):
    def __init__(self, reason: str, *, provider_key: str | None = None):
        self.reason = reason
        self.provider_key = provider_key
        msg = f"Invalid provider class: {reason}. Ensure that the class was obtained using the .providers.get_provider_by_key function"
        if provider_key:
            msg += f" | provider_key={provider_key}"
        super().__init__(msg)


class Fiction(NamedTuple):
    uid: str
    source_url: Url

    meta: BookConfig
    chapter_refs: list[ChapterRef]


class Provider(ABC):
    """Stateless provider interface. No ORM/DTO state inside."""

    @classmethod
    def provider_key(cls) -> str:
        if not hasattr(cls, "_provider_key"):
            raise InvalidProviderError(reason="Missing '_provider_key' attribute.")

        key = getattr(cls, "_provider_key")

        if not isinstance(key, str):
            raise InvalidProviderError(
                reason="'_provider_key' attribute is not of type `str`.",
                provider_key=key,
            )

        return key

    # Identify a fiction (used for upsert / dedupe)
    @abstractmethod
    def fiction_uid_from_url(self, url: str) -> str | None: ...

    # Discover metadata + ToC from a fiction page
    @abstractmethod
    async def discover_fiction(
        self, dm: AsyncDownloadManager, fiction_url: Url, *, ignore_cache: bool = False
    ) -> Fiction: ...

    # Extract a chapter’s title+content from its page
    @abstractmethod
    def extract_chapter(
        self, soup: BeautifulSoup, *, options: Optional[ExtractOptions] = None
    ) -> ChapterPageContent | None: ...

    ### Maybe
    # @abstractmethod
    # def canonical_chapter_url(self, chapter_id_prefixed: str) -> str: ...
