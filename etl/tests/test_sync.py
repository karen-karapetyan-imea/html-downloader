"""Sync discovery and resume behaviour against MongoDB."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from artists.spec import ARTISTS
from artworks.__main__ import main as artwork_main
from etl_core import sync as sync_module
from etl_core.pipeline import RunConfig
from etl_core.store import EntityStore, MongoConfig
from etl_core.sync import SyncConfig, find_pending, sync
from tests.builders import make_crawl, saatchi_pages, write_crawl_manifest


def crawl(
    data_root: Path,
    crawl_date: str = "2026-09-23",
    status: str | None = "completed",
    platform: str = "saatchi",
    **manifest,
) -> Path:
    data = make_crawl(data_root / platform, crawl_date, saatchi_pages())
    if status is not None:
        write_crawl_manifest(data, status=status, **manifest)
    return data


def pending(data_root: Path, mongo: MongoConfig, **kw) -> list[tuple[str, str, bool]]:
    return [
        (p.platform, p.crawl_date.isoformat(), p.resume)
        for p in find_pending(ARTISTS, data_root, mongo, **kw)
    ]


def config(data_root: Path, mongo: MongoConfig, **kw) -> SyncConfig:
    return SyncConfig(data_root=data_root, mongo=mongo, workers=1, chunk_size=2, **kw)


def test_finished_crawls_are_pending_and_running_ones_are_not(mongo_config: MongoConfig, tmp_path):
    crawl(tmp_path / "data", "2026-08-01", status="completed")
    crawl(tmp_path / "data", "2026-08-08", status="failed")
    crawl(tmp_path / "data", "2026-08-15", status="running", finished_at=None)

    assert pending(tmp_path / "data", mongo_config) == [
        ("saatchi", "2026-08-01", False),
        ("saatchi", "2026-08-08", False),
    ]


def test_crawls_without_manifest_need_opt_in(mongo_config: MongoConfig, tmp_path):
    crawl(tmp_path / "data", status=None)
    assert pending(tmp_path / "data", mongo_config) == []
    assert pending(tmp_path / "data", mongo_config, include_unmanaged=True) == [
        ("saatchi", "2026-09-23", False)
    ]


def test_unknown_platforms_undated_folders_and_bad_manifests_are_ignored(
    mongo_config: MongoConfig, tmp_path
):
    data_root = tmp_path / "data"
    crawl(data_root, platform="singulart")
    (data_root / "saatchi" / "latest").mkdir(parents=True)
    (data_root / "saatchi" / "2026-13-45").mkdir()
    crawl(data_root, "2026-09-23")
    (data_root / "saatchi" / "2026-09-23" / "manifest.json").write_text("{half writ")

    assert pending(data_root, mongo_config) == []


def test_platform_filter(mongo_config: MongoConfig, tmp_path):
    crawl(tmp_path / "data")
    assert pending(tmp_path / "data", mongo_config, platforms=("artsy",)) == []
    with pytest.raises(ValueError, match="unknown platform"):
        pending(tmp_path / "data", mongo_config, platforms=("nope",))


def test_sync_builds_mains_then_does_nothing(
    store: EntityStore, mongo_config: MongoConfig, tmp_path
):
    data_root = tmp_path / "data"
    crawl(data_root, "2026-08-01")
    crawl(data_root, "2026-09-23")

    result = sync(ARTISTS, config(data_root, mongo_config))

    assert result.ok
    assert [p.crawl_date.isoformat() for p in result.processed] == ["2026-08-01", "2026-09-23"]
    assert result.compacted == ["saatchi"]
    assert store.count_mains("artists", "saatchi") == 2

    again = sync(ARTISTS, config(data_root, mongo_config))
    assert (again.pending, again.compacted) == ([], [])


def test_crawl_finished_after_the_etl_run_is_redone(
    store: EntityStore, mongo_config: MongoConfig, tmp_path
):
    data_root = tmp_path / "data"
    data = crawl(data_root)
    sync(ARTISTS, config(data_root, mongo_config))

    write_crawl_manifest(data, finished_at="2099-01-01T00:00:00Z")
    assert pending(data_root, mongo_config) == [("saatchi", "2026-09-23", False)]
    assert sync(ARTISTS, config(data_root, mongo_config)).compacted == ["saatchi"]
    assert pending(data_root, mongo_config) == [("saatchi", "2026-09-23", False)]


def test_interrupted_snapshot_is_resumed(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    from itertools import batched

    from etl_core.crawl import iter_crawl_files, load_crawl_log
    from etl_core.sources import get_source, parser_fingerprint
    from etl_core.store import CrawlIdentity
    from etl_core.writer import process_chunk

    data_root = tmp_path / "data"
    data = crawl(data_root)
    source = get_source("saatchi")
    crawl_id, _ = store.prepare_crawl(
        CrawlIdentity("artists", "saatchi", str(data.resolve()), 2),
        date(2026, 9, 23),
        resume=False,
        parser={"path": str(source.parser_path), "sha256": parser_fingerprint("saatchi")},
    )
    crawl_log = load_crawl_log(data)
    for index, files in enumerate(batched(iter_crawl_files(data, crawl_log), 2)):
        if index >= 2:
            break
        process_chunk(
            ARTISTS, "saatchi", index, list(files), crawl_id, date(2026, 9, 23),
            mongo_config.uri, mongo_config.database,
        )

    assert pending(data_root, mongo_config) == [("saatchi", "2026-09-23", True)]
    result = sync(ARTISTS, config(data_root, mongo_config))
    assert result.ok
    ledger = store.get_crawl(crawl_id)
    assert ledger["status"] == "completed"
    assert ledger["stats"]["resumed_chunks"] == 2


def test_resume_with_other_settings_starts_over(
    store: EntityStore, mongo_config: MongoConfig, tmp_path
):
    from itertools import batched

    from etl_core.crawl import iter_crawl_files, load_crawl_log
    from etl_core.sources import get_source, parser_fingerprint
    from etl_core.store import CrawlIdentity
    from etl_core.writer import process_chunk

    data_root = tmp_path / "data"
    data = crawl(data_root)
    source = get_source("saatchi")
    store.prepare_crawl(
        CrawlIdentity("artists", "saatchi", str(data.resolve()), 3),
        date(2026, 9, 23),
        resume=False,
        parser={"path": str(source.parser_path), "sha256": parser_fingerprint("saatchi")},
    )
    crawl_log = load_crawl_log(data)
    for index, files in enumerate(batched(iter_crawl_files(data, crawl_log), 3)):
        if index >= 1:
            break
        process_chunk(
            ARTISTS, "saatchi", index, list(files), "artists:saatchi:2026-09-23",
            date(2026, 9, 23), mongo_config.uri, mongo_config.database,
        )

    assert sync(ARTISTS, config(data_root, mongo_config)).ok
    ledger = store.get_crawl("artists:saatchi:2026-09-23")
    assert ledger["chunk_size"] == 2
    assert ledger["status"] == "completed"


def test_failed_crawl_does_not_block_the_others(
    store: EntityStore, mongo_config: MongoConfig, tmp_path, monkeypatch
):
    data_root = tmp_path / "data"
    crawl(data_root, "2026-08-01")
    crawl(data_root, "2026-09-23")
    real_run = sync_module.run_snapshot

    def flaky_run(run_config: RunConfig):
        if run_config.crawl_date == date(2026, 8, 1):
            raise OSError("disk on fire")
        return real_run(run_config)

    monkeypatch.setattr(sync_module, "run_snapshot", flaky_run)
    result = sync(ARTISTS, config(data_root, mongo_config))

    assert not result.ok
    assert [p.crawl_date for p in result.failed] == [date(2026, 8, 1)]
    assert [p.crawl_date for p in result.processed] == [date(2026, 9, 23)]
    assert result.compacted == ["saatchi"]
    assert pending(data_root, mongo_config) == [("saatchi", "2026-08-01", False)]


def test_dry_run_writes_nothing(mongo_config: MongoConfig, tmp_path):
    crawl(tmp_path / "data")
    result = sync(ARTISTS, config(tmp_path / "data", mongo_config, dry_run=True))
    assert len(result.pending) == 1
    assert (result.processed, result.compacted) == ([], [])
    store = EntityStore(mongo_config)
    try:
        assert store.count_mains("artists", "saatchi") == 0
    finally:
        store.close()


def test_cli_sync_exit_codes(store: EntityStore, mongo_config: MongoConfig, tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    crawl(data_root)
    args = [
        "sync",
        "--data-root",
        str(data_root),
        "--mongo-uri",
        mongo_config.uri,
        "--mongo-db",
        mongo_config.database,
        "--workers",
        "1",
    ]

    assert artwork_main(args) == 0
    assert store.count_mains("artworks", "saatchi") == 1

    def broken_run(run_config: RunConfig):
        raise OSError("boom")

    crawl(data_root, "2026-09-30")
    monkeypatch.setattr(sync_module, "run_snapshot", broken_run)
    assert artwork_main(args) == 1
