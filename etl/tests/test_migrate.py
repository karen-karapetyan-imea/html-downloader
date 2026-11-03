"""Parquet snapshot -> MongoDB migrate, including etl_crawls ledger marking."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from artists.schema import ArtistRow
from artists.spec import ARTISTS
from etl_core.layout import MANIFEST_NAME, atomic_write_table, snapshot_dir
from etl_core.migrate import migrate_parquet
from etl_core.schema import to_table
from etl_core.store import STATUS_COMPLETED, EntityStore, MongoConfig
from etl_core.sync import find_pending


def _write_artist_snapshot(out: Path, crawl_date: date, rows: list[ArtistRow]) -> None:
    snap = snapshot_dir(out, "saatchi", crawl_date)
    snap.mkdir(parents=True)
    atomic_write_table(snap / "part-00000.parquet", to_table(ARTISTS.schema, rows))
    (snap / MANIFEST_NAME).write_text(
        json.dumps({"dataset": "artists", "platform": "saatchi", "rows": len(rows)}),
        encoding="utf-8",
    )


def test_migrate_upserts_versions_mains_and_ledger(
    store: EntityStore, mongo_config: MongoConfig, tmp_path: Path
):
    out = tmp_path / "artists"
    older = [
        ArtistRow(
            "1",
            full_name="Old",
            city="OldCity",
            source_file="a.html",
            crawled_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    ]
    newer = [
        ArtistRow(
            "1",
            full_name="New",
            city="NewCity",
            source_file="a.html",
            crawled_at=datetime(2026, 9, 23, tzinfo=UTC),
        ),
        ArtistRow(
            "2",
            full_name="Other",
            city="Berlin",
            source_file="b.html",
            crawled_at=datetime(2026, 9, 23, tzinfo=UTC),
        ),
    ]
    _write_artist_snapshot(out, date(2026, 8, 1), older)
    _write_artist_snapshot(out, date(2026, 9, 23), newer)

    totals = migrate_parquet(ARTISTS, out, mongo_config)
    assert totals == {"saatchi": 3}  # 1 + 2 winners published across two dates

    main = store.find_main("artists", "saatchi", "1")
    assert main is not None
    assert main["latest_version"] == "2026-09-23"
    assert main["data"]["city"] == "NewCity"
    assert len(store.list_versions("artists", "saatchi", "1")) == 2

    ledger = store.get_crawl("artists:saatchi:2026-09-23")
    assert ledger is not None
    assert ledger["status"] == STATUS_COMPLETED
    assert ledger["migrated_from"] == "parquet"
    assert ledger["stats"]["rows"] == 2

    # Unmanaged HTML sync must not re-queue a migrated crawl date.
    data_root = tmp_path / "data"
    crawl_dir = data_root / "saatchi" / "2026-09-23"
    (crawl_dir / "html").mkdir(parents=True)
    (crawl_dir / "html" / "x.html").write_text("<html></html>", encoding="utf-8")
    pending = find_pending(ARTISTS, data_root, mongo_config, include_unmanaged=True)
    assert pending == []
