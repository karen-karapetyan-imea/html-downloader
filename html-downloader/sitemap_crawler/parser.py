"""HTML parsing and sitemap discovery for 1stDibs."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from collections.abc import Callable

from sitemap_crawler.normalizer import normalize_sitemap_page_url, normalize_url

_HOMEPAGE = "https://www.1stdibs.com/"
ART_SITEMAP_SEEDS = (
    "https://www.1stdibs.com/sitemap/art/items/",
    "https://www.1stdibs.com/sitemap/art/dealers/",
    "https://www.1stdibs.com/sitemap/art/creators/",
)
_ART_SITEMAP_PREFIXES = (
    "/sitemap/art/items/",
    "/sitemap/art/dealers/",
    "/sitemap/art/creators/",
)
_SKIP_HREF_PREFIXES = ("mailto:", "tel:", "javascript:", "data:")
_SITEMAP_MARKER_RE = re.compile(r"sitemap", re.IGNORECASE)


def is_sitemap_page(url: str) -> bool:
    """Return True if URL is a 1stDibs HTML sitemap page."""
    normalized = normalize_sitemap_page_url(url)
    if normalized is None:
        return False
    path = urlsplit(normalized).path
    return path == "/sitemap/" or path.startswith("/sitemap/")


def is_art_sitemap_page(url: str) -> bool:
    """Return True if URL is within the art HTML sitemap branches."""
    normalized = normalize_sitemap_page_url(url)
    if normalized is None:
        return False
    path = urlsplit(normalized).path
    return any(
        path == prefix.rstrip("/") + "/" or path.startswith(prefix)
        for prefix in _ART_SITEMAP_PREFIXES
    )


def discover_sitemap_root(
    homepage_html: str,
    homepage_url: str = _HOMEPAGE,
    robots_text: str = "",
) -> str | None:
    """Discover the HTML sitemap root URL from homepage and robots.txt."""
    candidates: list[str] = []

    for href in _extract_href_values(homepage_html):
        if "sitemap" not in href.lower():
            continue
        if href.lower().endswith(".xml"):
            continue
        absolute = urljoin(homepage_url, href)
        normalized = normalize_sitemap_page_url(absolute)
        if normalized and is_sitemap_page(normalized):
            candidates.append(normalized)

    for line in robots_text.splitlines():
        line = line.strip()
        if not line.lower().startswith("sitemap:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value.lower().endswith(".xml"):
            continue
        normalized = normalize_sitemap_page_url(value)
        if normalized and is_sitemap_page(normalized):
            candidates.append(normalized)

    if not candidates:
        return None

    for candidate in candidates:
        if urlsplit(candidate).path.rstrip("/") == "/sitemap":
            return normalize_sitemap_page_url(candidate)

    candidates.sort(key=lambda u: (u.count("/"), len(u)))
    return candidates[0]


def parse_page_links(
    html: str,
    page_url: str,
    *,
    sitemap_filter: Callable[[str], bool] | None = None,
) -> tuple[list[str], list[str]]:
    """Extract all internal URLs and sitemap child URLs from a page."""
    internal_urls: list[str] = []
    sitemap_children: list[str] = []
    seen_internal: set[str] = set()
    seen_sitemap: set[str] = set()
    filter_sitemap = sitemap_filter or is_art_sitemap_page

    for href in _extract_href_values(html):
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(_SKIP_HREF_PREFIXES):
            continue

        absolute = urljoin(page_url, href)
        normalized = normalize_url(absolute)
        if normalized is None:
            continue

        if normalized not in seen_internal:
            seen_internal.add(normalized)
            internal_urls.append(normalized)

        sitemap_url = normalize_sitemap_page_url(absolute)
        if (
            sitemap_url
            and filter_sitemap(sitemap_url)
            and sitemap_url not in seen_sitemap
        ):
            seen_sitemap.add(sitemap_url)
            sitemap_children.append(sitemap_url)

    return internal_urls, sitemap_children


def looks_like_sitemap_html(html: str) -> bool:
    """Return True if HTML appears to be a 1stDibs sitemap page."""
    lower = html.lower()
    if "/sitemap/" in lower or "sitemap" in lower:
        return True
    return bool(_SITEMAP_MARKER_RE.search(html[:4096]))


def _extract_href_values(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        if isinstance(href, str):
            hrefs.append(href.strip())
    return hrefs
