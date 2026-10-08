"""One mapper per platform: the platform parser's output -> ArtworkRows.

Each mapper module exposes:
    is_relevant(parsed) -> bool                                  pages that carry artworks (cards or detail)
    to_rows(parsed, source_file, crawled_at) -> list[ArtworkRow] records without an id are dropped

Mappers only fill a column when the parser exposes that value; they never guess.
"""

from typing import cast

from artworks.mappers import artfinder, artmajeur, artsper, artsy, saatchi
from artworks.schema import ArtworkRow
from etl_core.dataset import Mapper

MAPPERS: dict[str, Mapper[ArtworkRow]] = {
    module.__name__.rsplit(".", 1)[-1]: cast(Mapper[ArtworkRow], module)
    for module in (artfinder, artmajeur, artsper, artsy, saatchi)
}
