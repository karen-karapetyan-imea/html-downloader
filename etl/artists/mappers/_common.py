from collections.abc import Mapping
from typing import Any


def own_artist_row(parsed: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """For {rows, replace} parsers: the artists row that came from the artist's own page."""
    artist_id = parsed.get("artist_id")
    for row in (parsed.get("rows") or {}).get("artists") or []:
        if row.get("has_page") and row.get("artist_id") == artist_id:
            return row
    return None


def image_url(value: object) -> object:
    """JSON-LD images may be a URL, an ImageObject or a list of either."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, Mapping):
        return value.get("url") or value.get("contentUrl")
    return value
