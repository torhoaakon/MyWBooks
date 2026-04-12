from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple, override
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from pydantic_core import Url

from mywbooks.book import BookConfig, ChapterRef
from mywbooks.async_download_manager import AsyncDownloadManager
from mywbooks.ebook_generator import (
    ChapterPageContent,
    ChapterPageExtractor,
    ExtractOptions,
)

from .base import Fiction, Provider

# ================= Header =================

PROVIDER_SHORT_NAME = "RoyalRoad"

# ==========================================


CHAPTER_ID_RE = re.compile(r"/chapter/(\d+)", re.IGNORECASE)
FICTION_ID_RE = re.compile(r"/fiction/(\d+)(?:/|$)", re.IGNORECASE)


# ----------------- Exceptions -----------------


class ChapterParseError(Exception):
    def __init__(self, reason: str, *, url: str | None, tried_selectors: list[str]):
        self.reason = reason
        self.url = url
        self.tried_selectors = tried_selectors
        msg = f"Chapter parse failed: {reason}. Tried selectors: {tried_selectors}"
        if url:
            msg += f" | url={url}"
        super().__init__(msg)


class FictionParseError(Exception):
    def __init__(self, reason: str, *, url: str | None, tried_selectors: list[str]):
        self.reason = reason
        self.url = url
        self.tried_selectors = tried_selectors
        msg = f"Fiction parse failed: {reason}. Tried selectors: {tried_selectors}"
        if url:
            msg += f" | url={url}"
        super().__init__(msg)


# ----------------- Provider -----------------


class RoyalRoadProvider(Provider):
    def __init__(self) -> None:
        self._extractor = RoyalRoadChapterPageExtractor()

    def fiction_uid_from_url(self, url: str) -> str | None:
        m = FICTION_ID_RE.search(urlparse(url).path)
        if not m:
            return None
        return f"{self.provider_key()}:{m.group(1)}"

    def fiction_url_from_uid(self, uid: str) -> str:
        [_, _id] = uid.split(":", 1)
        return f"https://www.royalroad.com/fiction/{_id}"

    async def discover_fiction(self, dm: AsyncDownloadManager, fiction_url: Url) -> Fiction:

        uid = self.fiction_uid_from_url(str(fiction_url))
        if not uid:
            raise ValueError("Could not extract RoyalRoad fiction id from URL")

        fiction_url = Url(self.fiction_url_from_uid(uid))

        data = await dm.get_and_cache_data(fiction_url)
        html = data.decode("utf-8")
        meta, chapter_entries = _parse_fiction_page(str(fiction_url), html, strict=True)

        def chapter_uid_from_url(url: Url) -> str:
            lid = _chapter_id_from_url(url)
            if lid is None:
                raise RuntimeError(
                    "RoyalRoadProvider failed to identify chapter id from url"
                )
            return f"{self.provider_key()}:{lid}"

        refs = [
            ChapterRef(
                id=chapter_uid_from_url(Url(u)),
                url=Url(u),
                title=title,
            )
            for u, title in chapter_entries
        ]
        return Fiction(
            uid=uid,
            source_url=fiction_url,
            meta=meta,
            chapter_refs=refs,
        )

    def extract_chapter(
        self, soup: BeautifulSoup, *, options: Optional[ExtractOptions] = None
    ) -> ChapterPageContent | None:
        return self._extractor.extract_chapter(soup, options=options)

    # def canonical_chapter_url(self, chapter_id_prefixed: str) -> str:
    #     return _canonical_rr_chapter_url(chapter_id_prefixed)


# ----------------- Extractor -----------------


class RoyalRoadChapterPageExtractor(ChapterPageExtractor):
    """
    Extract title + content from a RoyalRoad chapter page.
    Now tries multiple selectors (id or class) and gives clear errors.
    """

    # broadened sets; order matters (earlier = preferred)
    TITLE_SELECTORS: list[str] = [
        "div.fic-header h1",
        "#chapter-inner h1",
        "#chapter-content h1",
        ".chapter-inner h1",
        # "article h1",
        # "h1",
    ]

    CONTENT_SELECTORS: list[str] = [
        "div.chapter-inner",
        "#chapter-inner",
        "#chapter-content",
        "div#chapter-content",
        "article.chapter-content",
        "div.chapter-content",
        # "article",
        # "main",
    ]

    # fiction_title_selector: str = "div.fic-header h1"
    # fiction_content_selector: str = "div.chapter-inner"

    def _first_match(
        self, soup: BeautifulSoup | Tag, selectors: Iterable[str]
    ) -> Tuple[Tag | None, str | None]:
        for sel in selectors:
            node = soup.select_one(sel)
            if node is not None:
                return node, sel
        return None, None

    @override
    def extract_chapter(
        self,
        page_content_bs: BeautifulSoup,
        *,
        options: ExtractOptions | None = None,
    ) -> Optional[ChapterPageContent]:
        opts = options or ExtractOptions()

        # title
        title_node, _ = self._first_match(page_content_bs, self.TITLE_SELECTORS)
        title = title_node.get_text(strip=True) if title_node else None

        # content
        content_node, _ = self._first_match(page_content_bs, self.CONTENT_SELECTORS)

        if not content_node:
            if opts.strict:
                raise ChapterParseError(
                    reason="Could not locate chapter content",
                    url=opts.url,
                    tried_selectors=self.CONTENT_SELECTORS,
                )
            return None

        if not title:
            if opts.fallback_title:
                title = opts.fallback_title
            else:
                inner = content_node.select_one("h1, h2")
                if inner:
                    title = inner.get_text(strip=True)
                elif opts.strict:
                    raise ChapterParseError(
                        "Could not locate chapter title",
                        url=opts.url,
                        tried_selectors=self.TITLE_SELECTORS,
                    )
                else:
                    title = "Untitled Chapter"

        # TODO: We also need to do this:
        # hidden_class_names = self.identify_hiddden_class_names(soup)
        # self.dispose_hidden_elements(hidden_class_names, inner_content)

        return ChapterPageContent(title=title, content=content_node)

    def identify_hiddden_class_names(self, bs: BeautifulSoup) -> list[str]:
        hidden_class_names: list[str] = []

        # TODO: replace this assert
        assert bs.head is not None

        for style_tag in bs.head.find_all("style"):
            css_content = style_tag.get_text()
            # Use regular expressions to find the class name with display: none;
            pattern = r"\.(.*?)\s*{[^}]*display:\s*none;[^}]*}"
            match: Optional[re.Match[str]] = re.search(pattern, css_content)
            if match:
                hidden_class_names.append(match.group(1))

        return hidden_class_names

    def dispose_hidden_elements(
        self, hidden_class_names: list[str], content_bs: Tag
    ) -> None:

        for class_name in hidden_class_names:
            for element in content_bs.find_all(class_=class_name):
                element.extract()


# ----------------- helpers -----------------


def _chapter_id_from_url(url: Url) -> str | None:
    """
    Extract a RoyalRoad chapter id and return a *namespaced* id, e.g. "royalroad:1269041".
    """
    m = CHAPTER_ID_RE.search(str(url))
    if not m:
        return None
    return m.group(1)


## ---- Private ----


def _parse_fiction_page(
    base_url: str,
    html: str,
    chapter_toc_strategies: int = 0,
    strict: bool = True,
) -> tuple[BookConfig, list[tuple[str, str | None]]]:
    """
    Extract book metadata (title, author, cover, language) and all chapter URLs
    from a RoyalRoad fiction page.
    """
    bs = BeautifulSoup(html, "lxml")

    # Title
    h1 = bs.select_one("div.fic-header h1") or bs.select_one("h1")
    title = h1.get_text(strip=True) if h1 else "Untitled"

    # Author
    author_tag = bs.select_one('a[href*="/profile/"], a[href*="/author/"]')
    author = author_tag.get_text(strip=True) if author_tag else "Unknown"

    # Cover
    cover_img = (
        bs.select_one("div.fic-header img")
        or bs.select_one('img[src*="royalroadcdn"]')
        or bs.select_one("img")
    )
    cover_src = (
        str(cover_img["src"]).strip()
        if (cover_img and cover_img.has_attr("src"))
        else "/favicon.ico"
    )
    cover = urljoin(base_url, cover_src)

    # Language
    lang_meta = bs.select_one(
        'meta[http-equiv="content-language"], meta[name="language"]'
    )
    language = str(
        lang_meta.get("content") if lang_meta and lang_meta.get("content") else "en"
    ).lower()

    # Description
    desc_node = bs.select_one("div.description")
    description = desc_node.get_text(separator="\n", strip=True) if desc_node else None

    # Tags / genres
    tag_nodes = bs.select("span.tags a, a.fiction-tag")
    tags: list[str] | None = [t.get_text(strip=True) for t in tag_nodes] or None

    # Chapters:
    chapter_links = _extract_toc_chapter_links(
        base_url, bs, strict=strict, strategies=chapter_toc_strategies
    )

    meta = BookConfig(
        title=title,
        language=language,
        author=author,
        cover_image=Url(cover),
        description=description,
        tags=tags,
    )
    return meta, chapter_links


def _extract_toc_chapter_links(
    base_url: str, bs: BeautifulSoup, *, strategies: int = 0, strict: bool
) -> list[tuple[str, str | None]]:
    """
    Try multiple strategies to find chapter links. If none found and strict=True,
    raise FictionParseError with selectors tried.
    Returns (url, title) pairs.
    """
    tried: list[str] = []

    strategies = strategies or 2  ## 0 means default:  2

    # 1) Canonical ToC container
    toc = bs.select_one("#chapters")
    tried.append("#chapters")
    if toc:
        links = _collect_rr_chapter_links(base_url, toc)
        if links:
            return links

    # 2) Common table/list containers people see on RR skins
    if strategies >= 2:
        selection = "div.chapters, div.chapter-list, div.fic-contents, section"
        containers = bs.select(selection)
        tried.append(selection)
        for c in containers:
            links = _collect_rr_chapter_links(base_url, c)
            if links:
                return links

    # 3) Global fallback: any anchor with /chapter/ anywhere in the page
    if strategies >= 3:
        tried.append('a[href*="/chapter/"] (global)')
        links = _collect_rr_chapter_links(base_url, bs)
        if links:
            return links

    if strict:
        raise FictionParseError(
            reason="Could not locate chapter table of contents",
            url=base_url,
            tried_selectors=tried,
        )

    return []


def _collect_rr_chapter_links(
    base_url: str, scope: BeautifulSoup | Tag
) -> list[tuple[str, str | None]]:
    """Return (url, title) pairs for each chapter link, in appearance order."""
    seen: dict[str, tuple[str, str | None]] = {}
    for a in scope.select('a[href*="/chapter/"]'):
        href = str(a.get("href", "")).strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        chap_id = _chapter_id_from_url(Url(full))
        if chap_id and chap_id not in seen:
            title = a.get_text(strip=True) or None
            seen[chap_id] = (full, title)

    return list(seen.values())  # preserves appearance order
