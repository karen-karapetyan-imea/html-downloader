"""`compact`: rebuild main documents for a source from historical versions (repair)."""

from __future__ import annotations

import logging
from typing import Any

from etl_core.dataset import DatasetSpec
from etl_core.sources import get_source
from etl_core.store import CompactResult, EntityStore, MongoConfig

log = logging.getLogger(__name__)


def compact(
    dataset: DatasetSpec[Any], platform: str, mongo: MongoConfig
) -> CompactResult:
    dataset.mapper(platform)
    get_source(platform)
    store = EntityStore(mongo)
    try:
        result = store.compact_source(dataset.name, platform)
    finally:
        store.close()
    log.info(
        "%s: %d crawl dates, current has %d %s rows",
        platform,
        result.snapshots,
        result.current_rows,
        dataset.name,
    )
    return result
