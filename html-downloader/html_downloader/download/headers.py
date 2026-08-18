"""Optional browser-like header overrides. curl_cffi impersonate="chrome" sets
Accept, Sec-Ch-Ua, etc. We only add overrides that don't break the fingerprint.
"""

from typing import Any

DEFAULT_HEADERS: dict[str, str] = {
    "Accept-Language": "en-US,en;q=0.9",
}


def get_headers(extra: dict[str, str] | None = None) -> dict[str, Any]:
    """Return dict of optional headers to pass to session.get(headers=...)."""
    out = dict(DEFAULT_HEADERS)
    if extra:
        out.update(extra)
    return out
