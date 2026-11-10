"""Keep only the two newest fully uploaded HTML crawl folders."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from etl_core.documents import crawl_ledger_id
from etl_core.html_retention import (
    AUCTION_DATASETS,
    MARKETPLACE_DATASETS,
    datasets_for_platform,
    prune_html_folders,
)
from etl_core.store import STATUS_COMPLETED, EntityStore, MongoConfig


def _mark_completed(store: EntityStore, dataset: str, platform: str, crawl_date: str) -> None:
    now = datetime.now(UTC)
    store.crawls().replace_one(
        {"_id": crawl_ledger_id(dataset, platform, crawl_date)},
        {
            "_id": crawl_ledger_id(dataset, platform, crawl_date),
            "dataset": dataset,
            "source": platform,
            "crawl_date": crawl_date,
            "status": STATUS_COMPLETED,
            "started_at": now,
            "finished_at": now,
        },
        upsert=True,
    )


def _folder(root: Path, platform: str, name: str) -> Path:
    path = root / platform / name
    path.mkdir(parents=True)
    (path / "html").mkdir()
    (path / "manifest.json").write_text('{"status": "completed"}\n', encoding="utf-8")
    return path


def test_datasets_for_platform() -> None:
    assert datasets_for_platform("saatchi", "artworks") == MARKETPLACE_DATASETS
    assert datasets_for_platform("saatchi", "artists") == MARKETPLACE_DATASETS
    assert datasets_for_platform("christies", "lots") == AUCTION_DATASETS
    assert datasets_for_platform("barnebys", "artworks") == AUCTION_DATASETS


def test_keeps_two_newest_fully_uploaded_marketplace_folders(
    store: EntityStore, mongo_config: MongoConfig, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    old = _folder(root, "saatchi", "2026-11-10")
    mid = _folder(root, "saatchi", "2026-11-17")
    new = _folder(root, "saatchi", "2026-11-24")

    for date_s in ("2026-11-10", "2026-11-17", "2026-11-24"):
        _mark_completed(store, "artworks", "saatchi", date_s)
        _mark_completed(store, "artists", "saatchi", date_s)

    result = prune_html_folders(
        root, "saatchi", mongo_config, datasets=MARKETPLACE_DATASETS, keep=2
    )

    assert sorted(p.name for p in result.deleted) == ["2026-11-10"]
    assert not old.exists()
    assert mid.exists() and new.exists()
    assert {p.name for p in result.kept} == {"2026-11-17", "2026-11-24"}


def test_incomplete_artists_upload_is_not_deleted(
    store: EntityStore, mongo_config: MongoConfig, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    a = _folder(root, "saatchi", "2026-11-10")
    b = _folder(root, "saatchi", "2026-11-17")
    c = _folder(root, "saatchi", "2026-11-24")

    for date_s in ("2026-11-10", "2026-11-17", "2026-11-24"):
        _mark_completed(store, "artworks", "saatchi", date_s)
    _mark_completed(store, "artists", "saatchi", "2026-11-17")
    _mark_completed(store, "artists", "saatchi", "2026-11-24")
    # 2026-11-10 missing artists ledger -> incomplete, must stay even if older.

    result = prune_html_folders(
        root, "saatchi", mongo_config, datasets=MARKETPLACE_DATASETS, keep=2
    )

    assert result.deleted == []
    assert a.exists() and b.exists() and c.exists()
    assert {p.name for p in result.skipped_incomplete} == {"2026-11-10"}
    assert {p.name for p in result.kept} == {"2026-11-17", "2026-11-24"}


def test_two_folders_are_all_kept(
    store: EntityStore, mongo_config: MongoConfig, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    _folder(root, "saatchi", "2026-11-10")
    _folder(root, "saatchi", "2026-11-17")
    for date_s in ("2026-11-10", "2026-11-17"):
        _mark_completed(store, "artworks", "saatchi", date_s)
        _mark_completed(store, "artists", "saatchi", date_s)

    result = prune_html_folders(
        root, "saatchi", mongo_config, datasets=MARKETPLACE_DATASETS, keep=2
    )

    assert result.deleted == []
    assert {p.name for p in result.kept} == {"2026-11-10", "2026-11-17"}


def test_auction_month_folders_use_lots_ledger_only(
    store: EntityStore, mongo_config: MongoConfig, tmp_path: Path
) -> None:
    root = tmp_path / "auctions"
    old = _folder(root, "christies", "2026-09")
    mid = _folder(root, "christies", "2026-10")
    new = _folder(root, "christies", "2026-11")

    # Auction folder YYYY-MM maps to crawl_date first-of-month in the ledger.
    for date_s in ("2026-09-01", "2026-10-01", "2026-11-01"):
        _mark_completed(store, "lots", "christies", date_s)

    result = prune_html_folders(
        root, "christies", mongo_config, datasets=AUCTION_DATASETS, keep=2
    )

    assert sorted(p.name for p in result.deleted) == ["2026-09"]
    assert not old.exists()
    assert mid.exists() and new.exists()
