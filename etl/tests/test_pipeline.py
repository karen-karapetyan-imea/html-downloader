"""End-to-end pipeline tests against an isolated MongoDB database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from artists.__main__ import main
from artists.spec import ARTISTS
from artworks.__main__ import main as artwork_main
from artworks.spec import ARTWORKS
from etl_core.compact import compact as compact_dataset
from etl_core.crawl import detect_crawl_date, html_root, iter_crawl_files, load_crawl_log
from etl_core.documents import entity_id, version_id
from etl_core.pipeline import RunConfig, run_snapshot
from etl_core.sources import get_source, parser_fingerprint
from etl_core.store import (
    STATUS_COMPLETED,
    CrawlIdentity,
    EntityStore,
    MongoConfig,
    ResumeMismatchError,
)
from etl_core.writer import process_chunk
from tests import builders
from tests.builders import make_crawl, saatchi_pages


def _stage_partial_crawl(
    store: EntityStore,
    mongo: MongoConfig,
    data,
    *,
    chunk_size: int,
    stop_before_chunk: int,
    platform: str = "saatchi",
) -> str:
    """Process chunks [0, stop_before_chunk) and leave the crawl ledger in a resumable state."""
    source = get_source(platform)
    crawl_date = detect_crawl_date(data)
    crawl_id, _ = store.prepare_crawl(
        CrawlIdentity(
            dataset="artists",
            platform=platform,
            data_dir=str(data.resolve()),
            chunk_size=chunk_size,
        ),
        crawl_date,
        resume=False,
        parser={"path": str(source.parser_path), "sha256": parser_fingerprint(platform)},
    )
    crawl_log = load_crawl_log(data)
    from itertools import batched

    for index, files in enumerate(batched(iter_crawl_files(data, crawl_log), chunk_size)):
        if index >= stop_before_chunk:
            break
        process_chunk(
            ARTISTS,
            platform,
            index,
            list(files),
            crawl_id,
            crawl_date,
            mongo.uri,
            mongo.database,
        )
    assert html_root(data).is_dir()
    return crawl_id


def config(data, mongo: MongoConfig, **kw) -> RunConfig:
    return RunConfig(
        dataset=ARTISTS,
        platform="saatchi",
        data_dir=data,
        crawl_date=detect_crawl_date(data),
        mongo=mongo,
        **kw,
    )


def artwork_config(data, mongo: MongoConfig, **kw) -> RunConfig:
    return RunConfig(
        dataset=ARTWORKS,
        platform="saatchi",
        data_dir=data,
        crawl_date=detect_crawl_date(data),
        mongo=mongo,
        **kw,
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_run_writes_versions_and_mains(store: EntityStore, mongo_config: MongoConfig, tmp_path, workers):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    manifest = run_snapshot(config(data, mongo_config, workers=workers, chunk_size=2))

    assert manifest["files_total"] == 5
    assert manifest["rows"] == 2  # artists 1 and 2 (duplicate of 1 deduped)
    assert manifest["skipped"] == 1
    assert manifest["errors"] == 1

    assert store.count_mains("artists", "saatchi") == 2
    main_doc = store.find_main("artists", "saatchi", "1")
    assert main_doc is not None
    assert main_doc["_id"] == "saatchi:1"
    assert main_doc["latest_version"] == "2026-09-23"
    assert main_doc["data"]["city"] == "Berlin"

    versions = store.list_versions("artists", "saatchi", "1")
    assert len(versions) == 1
    assert versions[0]["_id"] == version_id(entity_id("saatchi", "1"), "2026-09-23")
    assert versions[0]["crawl_date"] == "2026-09-23"

    crawl = store.get_crawl("artists:saatchi:2026-09-23")
    assert crawl is not None
    assert crawl["status"] == STATUS_COMPLETED
    assert store.errors().count_documents({"crawl_id": "artists:saatchi:2026-09-23"}) == 1


def test_resume_redoes_only_missing_chunks(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    # Interrupt before finalize: process the first two chunks, leave the crawl running.
    _stage_partial_crawl(store, mongo_config, data, chunk_size=2, stop_before_chunk=2)

    second = run_snapshot(config(data, mongo_config, chunk_size=2, resume=True))
    assert second["resumed_chunks"] == 2
    assert second["rows"] == 2
    assert store.count_mains("artists", "saatchi") == 2


def test_resume_with_different_chunk_size_is_rejected(
    store: EntityStore, mongo_config: MongoConfig, tmp_path
):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    _stage_partial_crawl(store, mongo_config, data, chunk_size=2, stop_before_chunk=2)
    with pytest.raises(ResumeMismatchError, match="cannot resume"):
        run_snapshot(config(data, mongo_config, chunk_size=3, resume=True))


def test_fresh_run_replaces_crawl(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    run_snapshot(config(data, mongo_config, chunk_size=1))
    run_snapshot(config(data, mongo_config, chunk_size=5))
    assert store.count_mains("artists", "saatchi") == 2
    assert len(store.list_versions("artists", "saatchi", "1")) == 1


def test_newer_crawl_wins_and_older_does_not_clobber(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    old = make_crawl(
        tmp_path,
        "2026-08-01",
        {
            "a.html": builders.saatchi_artist(1, city="Old"),
            "z.html": builders.saatchi_artist(9, city="Gone"),
        },
    )
    new = make_crawl(tmp_path, "2026-09-23", saatchi_pages(city="New"))
    run_snapshot(config(old, mongo_config, chunk_size=1))
    run_snapshot(config(new, mongo_config, chunk_size=1))

    result = compact_dataset(ARTISTS, "saatchi", mongo_config)
    assert result.current_rows == 3

    rows = {
        doc["platform_id"]: doc["data"]["city"]
        for doc in store.list_mains("artists", "saatchi")
    }
    assert rows == {"1": "New", "2": "New", "9": "Gone"}
    assert store.find_main("artists", "saatchi", "1")["latest_version"] == "2026-09-23"
    assert [v["crawl_date"] for v in store.list_versions("artists", "saatchi", "1")] == [
        "2026-08-01",
        "2026-09-23",
    ]

    # Re-run the older crawl: must not overwrite the newer main.
    run_snapshot(config(old, mongo_config, chunk_size=1))
    assert store.find_main("artists", "saatchi", "1")["data"]["city"] == "New"
    assert store.find_main("artists", "saatchi", "1")["latest_version"] == "2026-09-23"


def test_same_crawl_reprocess_does_not_duplicate_versions(
    store: EntityStore, mongo_config: MongoConfig, tmp_path
):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    run_snapshot(config(data, mongo_config))
    run_snapshot(config(data, mongo_config))
    assert len(store.list_versions("artists", "saatchi", "1")) == 1


def test_compact_without_crawls_fails(mongo_config: MongoConfig):
    with pytest.raises(FileNotFoundError):
        compact_dataset(ARTISTS, "saatchi", mongo_config)


OLDER_THAN_ARTIST_PAGES_MS = 1700000000000


@pytest.mark.parametrize("workers", [1, 2])
def test_artworks_detail_page_beats_newer_card_within_a_crawl(
    store: EntityStore, mongo_config: MongoConfig, tmp_path, workers
):
    data = make_crawl(
        tmp_path,
        "2026-09-23",
        {
            "a.html": builders.saatchi_artist(artworks=[builders.saatchi_card(5), builders.saatchi_card(6)]),
            "b.html": builders.saatchi_artwork(server_ms=OLDER_THAN_ARTIST_PAGES_MS),
            "c.html": builders.saatchi_artist(2),
        },
    )
    manifest = run_snapshot(artwork_config(data, mongo_config, workers=workers, chunk_size=1))
    assert manifest["files_total"] == 3
    assert manifest["rows"] == 2
    assert manifest["no_id"] == 1

    assert compact_dataset(ARTWORKS, "saatchi", mongo_config).current_rows == 2
    art5 = store.find_main("artworks", "saatchi", "5")
    art6 = store.find_main("artworks", "saatchi", "6")
    assert art5["data"]["price"] == Decimal("1500.00")
    assert art5["data"]["has_detail"] is True
    assert art5["data"]["mediums"] == ["Oil"]
    assert art6["data"]["price"] == Decimal("1000.00")
    assert art6["data"]["has_detail"] is False


def test_artworks_newest_crawl_wins_across_crawls(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    old = make_crawl(tmp_path, "2026-08-01", {"b.html": builders.saatchi_artwork()})
    new = make_crawl(
        tmp_path, "2026-09-23", {"a.html": builders.saatchi_artist(artworks=[builders.saatchi_card(5)])}
    )
    run_snapshot(artwork_config(old, mongo_config))
    run_snapshot(artwork_config(new, mongo_config))
    compact_dataset(ARTWORKS, "saatchi", mongo_config)

    art = store.find_main("artworks", "saatchi", "5")
    assert art["latest_version"] == "2026-09-23"
    assert art["data"]["price"] == Decimal("1000.00")
    assert art["data"]["has_detail"] is False
    assert len(store.list_versions("artworks", "saatchi", "5")) == 2


def test_sources_stay_independent(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    """Same numeric platform id on two marketplaces must be two documents."""
    saatchi = make_crawl(tmp_path, "2026-09-23", {"a.html": builders.saatchi_artist(1, city="S")})
    # Reuse saatchi HTML under artsy folder only for identity wiring; mapper must still parse.
    # Prefer a second saatchi-shaped crawl registered as saatchi vs verifying entity_id helpers
    # already covered — run two saatchi ids and assert artsper-style ids are distinct in store.
    run_snapshot(config(saatchi, mongo_config))
    store.main("artists").insert_one(
        {
            "_id": "artsper:1",
            "source": "artsper",
            "platform_id": "1",
            "entity_type": "artist",
            "data": {"platform_artist_id": "1", "city": "A"},
            "latest_version": "2026-09-23",
        }
    )
    assert store.find_main("artists", "saatchi", "1")["data"]["city"] == "S"
    assert store.find_main("artists", "artsper", "1")["data"]["city"] == "A"
    saatchi_id = store.find_main("artists", "saatchi", "1")["_id"]
    artsper_id = store.find_main("artists", "artsper", "1")["_id"]
    assert saatchi_id != artsper_id


def test_artwork_cli_run_and_compact(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", {"b.html": builders.saatchi_artwork()})
    run = [
        "run",
        "--platform",
        "saatchi",
        "--data",
        str(data),
        "--mongo-uri",
        mongo_config.uri,
        "--mongo-db",
        mongo_config.database,
        "--workers",
        "1",
    ]
    assert artwork_main(run) == 0
    assert (
        artwork_main(
            [
                "compact",
                "--platform",
                "saatchi",
                "--mongo-uri",
                mongo_config.uri,
                "--mongo-db",
                mongo_config.database,
            ]
        )
        == 0
    )
    assert store.count_mains("artworks", "saatchi") == 1


def test_resume_of_another_dataset_is_rejected(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    crawl_id = _stage_partial_crawl(store, mongo_config, data, chunk_size=2, stop_before_chunk=2)
    store.crawls().update_one({"_id": crawl_id}, {"$set": {"dataset": "artworks"}})
    with pytest.raises(ResumeMismatchError, match="cannot resume"):
        run_snapshot(config(data, mongo_config, chunk_size=2, resume=True))


def test_crawl_log_and_date_detection(tmp_path):
    data = make_crawl(
        tmp_path,
        "2026-09-23",
        {"a.html": "<html></html>"},
        log=[
            {"filename": "a.html", "url": "https://x/1", "timestamp": "2026-09-23T10:00:00"},
            {"filename": "a.html", "url": "https://x/2", "timestamp": "2026-09-23T11:00:00+02:00"},
        ],
    )
    entry = load_crawl_log(data)["a.html"]
    assert entry.url == "https://x/2"
    assert entry.crawled_at.isoformat() == "2026-09-23T09:00:00+00:00"
    assert detect_crawl_date(data / "html") == date(2026, 9, 23)
    assert detect_crawl_date(tmp_path, "2026-01-02") == date(2026, 1, 2)
    month = tmp_path / "2026-10"
    month.mkdir()
    assert detect_crawl_date(month / "html") == date(2026, 10, 1)
    with pytest.raises(ValueError):
        detect_crawl_date(tmp_path / "undated")


def test_cli_run_and_compact(store: EntityStore, mongo_config: MongoConfig, tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    assert (
        main(
            [
                "run",
                "--platform",
                "saatchi",
                "--data",
                str(data),
                "--mongo-uri",
                mongo_config.uri,
                "--mongo-db",
                mongo_config.database,
                "--workers",
                "1",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "compact",
                "--platform",
                "saatchi",
                "--mongo-uri",
                mongo_config.uri,
                "--mongo-db",
                mongo_config.database,
            ]
        )
        == 0
    )
    assert store.count_mains("artists", "saatchi") == 2
    assert (
        main(
            [
                "run",
                "--platform",
                "saatchi",
                "--data",
                str(tmp_path / "missing"),
                "--mongo-uri",
                mongo_config.uri,
                "--mongo-db",
                mongo_config.database,
                "--crawl-date",
                "2026-01-01",
            ]
        )
        == 2
    )
