"""One mapper per platform: the platform parser's output -> ArtistRow.

Each mapper module exposes:
    is_artist_page(parsed) -> bool
    to_artist(parsed, source_file, crawled_at) -> ArtistRow | None   (None: artist page without an id)
"""

from collections.abc import Mapping
from datetime import datetime
from types import ModuleType
from typing import Any, Protocol, cast

from artists.mappers import artfinder, artmajeur, artsper, artsy, saatchi
from artists.schema import ArtistRow


class ArtistMapper(Protocol):
    def is_artist_page(self, parsed: Mapping[str, Any]) -> bool: ...

    def to_artist(
        self, parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None
    ) -> ArtistRow | None: ...


_MODULES: dict[str, ModuleType] = {
    "artfinder": artfinder,
    "artmajeur": artmajeur,
    "artsper": artsper,
    "artsy": artsy,
    "saatchi": saatchi,
}


def get_mapper(platform: str) -> ArtistMapper:
    try:
        return cast(ArtistMapper, _MODULES[platform])
    except KeyError:
        raise ValueError(f"no mapper for platform {platform!r}") from None
