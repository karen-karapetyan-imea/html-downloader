from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from artworks.schema import ArtworkRow
from etl_core.normalize import clean_text, currency_code, to_price

type Record = Mapping[str, Any]


def rows_of(parsed: Record, table: str) -> list[Record]:
    """For {rows, replace} parsers: all rows of one table."""
    return (parsed.get("rows") or {}).get(table) or []


def artist_names(parsed: Record) -> dict[Any, str]:
    """For {rows, replace} parsers: artist id -> name from every artists row on the page."""
    names: dict[Any, str] = {}
    for row in rows_of(parsed, "artists"):
        name = clean_text(row.get("name"))
        if name is not None:
            names.setdefault(row.get("artist_id"), name)
    return names


def priced(*candidates: tuple[object, object]) -> tuple[Decimal | None, str | None]:
    """First (amount, currency) pair with a usable amount; the currency belongs to that amount only."""
    for amount, currency in candidates:
        price = to_price(amount)
        if price is not None:
            return price, currency_code(currency)
    return None, None


def collect(
    records: Iterable[Record],
    build: Callable[[Record, dict[str, Any]], ArtworkRow | None],
    source_file: str,
    crawled_at: datetime | None,
) -> list[ArtworkRow]:
    """Map every artwork record; records without an id (build -> None) are dropped."""
    stamp = {"source_file": source_file, "crawled_at": crawled_at}
    return [row for record in records if (row := build(record, stamp)) is not None]
