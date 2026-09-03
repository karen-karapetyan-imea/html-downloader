"""URL normalization for 1stDibs sitemap crawler."""

from __future__ import annotations

from urllib.parse import parse_qsl, urldefrag, urlencode, urlsplit, urlunsplit

_ALLOWED_HOSTS = frozenset({"www.1stdibs.com", "1stdibs.com"})
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
    }
)


def normalize_url(url: str) -> str | None:
    """Normalize an absolute URL; return None for external or invalid URLs."""
    text = (url or "").strip()
    if not text:
        return None

    absolute, _fragment = urldefrag(text)
    parts = urlsplit(absolute)
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return None

    host = (parts.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        return None

    canonical_host = "www.1stdibs.com"
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(filtered_query, doseq=True)

    return urlunsplit((scheme, canonical_host, parts.path, query, ""))


def normalize_sitemap_page_url(url: str) -> str | None:
    """Normalize a sitemap page URL for visit tracking (path-only, trailing slash)."""
    normalized = normalize_url(url)
    if normalized is None:
        return None
    parts = urlsplit(normalized)
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
