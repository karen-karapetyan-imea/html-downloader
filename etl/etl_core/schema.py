"""Parquet helpers shared by every dataset.

`platform_name` and `crawl_date` are Hive partition columns: they live in the directory
names (`platform_name=<p>/crawl_date=<d>`), not inside the Parquet files.
"""

from collections.abc import Sequence
from dataclasses import fields, is_dataclass

import pyarrow as pa

PLATFORM_COLUMN = "platform_name"
CRAWL_DATE_COLUMN = "crawl_date"


def assert_in_sync(row_type: type, schema: pa.Schema) -> None:
    if not is_dataclass(row_type):
        raise TypeError(f"{row_type.__name__} must be a dataclass")
    if tuple(f.name for f in fields(row_type)) != tuple(schema.names):
        raise RuntimeError(f"{row_type.__name__} fields and its Parquet schema columns are out of sync")


def to_table(schema: pa.Schema, rows: Sequence[object]) -> pa.Table:
    return pa.table({name: [getattr(r, name) for r in rows] for name in schema.names}, schema=schema)
