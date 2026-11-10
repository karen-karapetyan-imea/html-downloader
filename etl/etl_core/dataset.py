"""What makes a dataset (artists, artworks, lots): row type, schema, mappers and dedup rules."""

import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pyarrow as pa

from etl_core.documents import SortField
from etl_core.schema import assert_in_sync


class Mapper[RowT](Protocol):
    """Platform parser output -> dataset rows. An empty list means a relevant page without usable ids."""

    def is_relevant(self, parsed: Mapping[str, Any]) -> bool: ...

    def to_rows(
        self, parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None
    ) -> list[RowT]: ...


@dataclass(frozen=True, slots=True)
class DatasetSpec[RowT]:
    name: str
    prog: str
    ref: str  # "module:ATTRIBUTE" where this spec is defined; workers re-import it from there
    row_type: type[RowT]
    schema: pa.Schema
    key_column: str
    mappers: Mapping[str, Mapper[RowT]]
    within_crawl_order: Sequence[SortField]
    default_out: str = "output"
    # Diff consecutive full crawls and record vanished entities in `private_sales`.
    private_sales: bool = False

    def __post_init__(self) -> None:
        assert_in_sync(self.row_type, self.schema)
        if self.key_column not in self.schema.names or self.schema.field(self.key_column).nullable:
            raise ValueError(f"{self.name}: key column {self.key_column!r} must be a non-nullable column")
        for field in self.within_crawl_order:
            if field.name not in self.schema.names:
                raise ValueError(f"{self.name}: sort field {field.name!r} is not a schema column")

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self.mappers))

    def mapper(self, platform: str) -> Mapper[RowT]:
        try:
            return self.mappers[platform]
        except KeyError:
            raise ValueError(
                f"{self.name}: unknown platform {platform!r}, expected one of {', '.join(self.platforms)}"
            ) from None

    def __reduce__(self) -> tuple[Any, tuple[str]]:
        """Pickle by reference: mappers are modules, which cannot cross process boundaries."""
        return load_dataset, (self.ref,)


def load_dataset(ref: str) -> DatasetSpec[Any]:
    module, _, attribute = ref.partition(":")
    spec = getattr(importlib.import_module(module), attribute)
    if not isinstance(spec, DatasetSpec):
        raise TypeError(f"{ref} is not a DatasetSpec")
    return spec
