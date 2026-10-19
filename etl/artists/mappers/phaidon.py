"""Phaidon crawl is product pages only; there are no artist profile pages to map."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.schema import ArtistRow


def is_artist_page(_parsed: Mapping[str, Any]) -> bool:
    return False


def to_artist(
    _parsed: Mapping[str, Any], _source_file: str, _crawled_at: datetime | None
) -> ArtistRow | None:
    return None
