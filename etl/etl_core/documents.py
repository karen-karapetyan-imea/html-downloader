"""Entity identity, BSON encoding, and within-crawl winner ordering for MongoDB."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128

ENTITY_TYPES = frozenset({"artist", "artwork", "lot"})


@dataclass(frozen=True, slots=True)
class SortField:
    """One column in the within-crawl winner order (matches the old DuckDB ORDER BY)."""

    name: str
    descending: bool = True
    nulls_last: bool = True


def entity_id(source: str, platform_id: str) -> str:
    return f"{source}:{platform_id}"


def version_id(entity: str, crawl_date: str) -> str:
    return f"{entity}:{crawl_date}"


def crawl_ledger_id(dataset: str, source: str, crawl_date: str) -> str:
    return f"{dataset}:{source}:{crawl_date}"


def stage_row_id(crawl_id: str, chunk_index: int, platform_id: str) -> str:
    return f"{crawl_id}:{chunk_index:05d}:{platform_id}"


def crawl_date_str(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def seen_at(crawled_at: datetime | None, crawl_date: str) -> datetime:
    """last_seen_at / first_seen_at from the winning row, falling back to the crawl date."""
    if crawled_at is not None:
        return crawled_at if crawled_at.tzinfo else crawled_at.replace(tzinfo=UTC)
    return datetime.fromisoformat(crawl_date).replace(tzinfo=UTC)


def row_to_data(row: object) -> dict[str, Any]:
    if not is_dataclass(row) or isinstance(row, type):
        raise TypeError(f"expected a dataclass instance, got {type(row)!r}")
    return {name: encode_value(getattr(row, name)) for name in (f.name for f in fields(row))}


def encode_value(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return Decimal128(str(value))
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, list):
        return [encode_value(item) for item in value]
    raise TypeError(f"cannot encode {type(value).__name__} for MongoDB")


def decode_value(value: object) -> Any:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    return value


def decode_data(data: dict[str, Any]) -> dict[str, Any]:
    return {key: decode_value(value) for key, value in data.items()}


def platform_id_of(row: object, key_column: str) -> str:
    value = getattr(row, key_column)
    if not isinstance(value, str) or not value:
        raise ValueError(f"key column {key_column!r} must be a non-empty string")
    return value


def winner_sort_key(row: object, order: Sequence[SortField]) -> tuple[Any, ...]:
    """Tuple for Python min()/sorted(): the smallest key is the within-crawl winner."""
    return tuple(_field_key(getattr(row, field.name, None), field) for field in order)


def doc_winner_sort_key(data: dict[str, Any], order: Sequence[SortField]) -> tuple[Any, ...]:
    return tuple(_field_key(_raw_from_data(data.get(field.name)), field) for field in order)


def pick_winner[T](rows: Sequence[T], order: Sequence[SortField]) -> T:
    if not rows:
        raise ValueError("cannot pick a winner from an empty sequence")
    return min(rows, key=lambda row: winner_sort_key(row, order))


def pick_winner_docs(docs: Sequence[dict[str, Any]], order: Sequence[SortField]) -> dict[str, Any]:
    if not docs:
        raise ValueError("cannot pick a winner from an empty sequence")
    return min(docs, key=lambda doc: doc_winner_sort_key(doc.get("data") or {}, order))


def dedup_rows[T](rows: Sequence[T], key_column: str, order: Sequence[SortField]) -> list[T]:
    """Keep one winning row per platform id, preserving first-seen key order."""
    best: dict[str, T] = {}
    order_keys: list[str] = []
    for row in rows:
        key = platform_id_of(row, key_column)
        previous = best.get(key)
        if previous is None:
            best[key] = row
            order_keys.append(key)
        elif winner_sort_key(row, order) < winner_sort_key(previous, order):
            best[key] = row
    return [best[key] for key in order_keys]


def _field_key(value: object, field: SortField) -> tuple[int, Any]:
    if value is None:
        return (1 if field.nulls_last else 0, None)
    ranked = _rank(value)
    if field.descending:
        ranked = _negate(ranked)
    return (0 if field.nulls_last else 1, ranked)


def _rank(value: object) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.timestamp()
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _negate(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return -value
    if isinstance(value, str):
        # Lexicographic DESC via code-point inversion; empty string sorts after non-empty.
        return (*(0x10FFFF - ord(ch) for ch in value), 0x110000)
    return value


def _raw_from_data(value: object) -> object:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    return value


def entity_type_for(dataset_name: str) -> str:
    singular = dataset_name[:-1] if dataset_name.endswith("s") else dataset_name
    if singular not in ENTITY_TYPES:
        raise ValueError(f"unknown entity type for dataset {dataset_name!r}")
    return singular


def collections_for(dataset_name: str) -> tuple[str, str]:
    """Return (main_collection, versions_collection)."""
    entity = entity_type_for(dataset_name)
    return dataset_name, f"{entity}_versions"
