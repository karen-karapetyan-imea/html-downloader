"""Private sales: artworks in one full crawl that are gone from the next full crawl."""

from __future__ import annotations

from decimal import Decimal

import pytest

from artists.schema import ArtistRow
from artworks.__main__ import main as artwork_main
from artworks.schema import ArtworkRow
from artworks.spec import ARTWORKS
from etl_core.documents import crawl_ledger_id
from etl_core.private_sales import detect_pending_private_sales, detect_private_sales
from etl_core.store import EntityStore, MongoConfig
from etl_core.sync import SyncConfig, sync
from tests.builders import make_crawl, saatchi_artist, saatchi_card, write_crawl_manifest

D1, D2, D3 = "2026-10-05", "2026-11-02", "2026-11-30"


def publish(
    store: EntityStore,
    crawl_date: str,
    ids: list[str],
    sold: dict[str, bool | None] | None = None,
    artists: dict[str, str] | None = None,
    **ledger,
) -> None:
    """Artworks for one crawl; is_sold is False unless `sold` says otherwise, no artist by default."""
    sold, artists = sold or {}, artists or {}
    rows = [
        ArtworkRow(
            platform_artwork_id=i,
            title=f"Work {i}",
            price=Decimal("100"),
            is_sold=sold.get(i, False),
            platform_artist_id=artists.get(i),
        )
        for i in ids
    ]
    store.publish_rows("artworks", "saatchi", crawl_date, rows, "platform_artwork_id", ())
    if ledger:
        store.crawls().update_one(
            {"_id": crawl_ledger_id("artworks", "saatchi", crawl_date)}, {"$set": ledger}
        )


def publish_artists(store: EntityStore, crawl_date: str, ids: list[str]) -> None:
    rows = [ArtistRow(platform_artist_id=i) for i in ids]
    store.publish_rows("artists", "saatchi", crawl_date, rows, "platform_artist_id", ())


def sales(store: EntityStore) -> dict[str, dict]:
    return {doc["platform_id"]: doc for doc in store.private_sales().find()}


def test_vanished_artworks_are_recorded_with_their_last_data(store: EntityStore):
    publish(store, D1, ["a", "b", "c", "d", "e"])
    publish(store, D2, ["a", "b", "d", "e", "f"])

    result = detect_private_sales(store, "artworks", "saatchi", D1, D2)

    assert (result.status, result.missing, result.marked_sold, result.written) == ("ok", 1, 0, 1)
    record = sales(store)["c"]
    assert record["_id"] == f"saatchi:c:{D2}"
    assert record["reason"] == "disappeared"
    assert (record["previous_crawl"], record["detected_in_crawl"]) == (D1, D2)
    assert "sold_data" not in record
    assert record["entity_type"] == "artwork"
    assert record["data"]["title"] == "Work c"
    ledger = store.get_crawl(crawl_ledger_id("artworks", "saatchi", D2))
    assert ledger["private_sales"]["previous_crawl"] == D1
    assert ledger["private_sales"]["missing"] == 1


def test_rerun_is_idempotent_and_follows_a_reprocessed_crawl(store: EntityStore):
    publish(store, D1, ["a", "b", "c", "d", "e"])
    publish(store, D2, ["a", "b", "d", "e"])
    detect_private_sales(store, "artworks", "saatchi", D1, D2)
    detect_private_sales(store, "artworks", "saatchi", D1, D2)
    assert list(sales(store)) == ["c"]

    store.versions("artworks").delete_many({"crawl_date": D2})
    publish(store, D2, ["a", "b", "c", "e"])
    detect_private_sales(store, "artworks", "saatchi", D1, D2)
    assert list(sales(store)) == ["d"]


def test_artworks_newly_marked_sold_are_recorded(store: EntityStore):
    publish(store, D1, ["a", "b", "c", "d", "e"], sold={"c": True, "d": None})
    publish(store, D2, ["a", "b", "c", "d", "e"], sold={"a": True, "c": True, "d": True})

    result = detect_private_sales(store, "artworks", "saatchi", D1, D2)

    # a: False -> True. c was already sold; d's earlier status was unknown.
    assert (result.missing, result.marked_sold, result.written) == (0, 1, 1)
    record = sales(store)["a"]
    assert record["reason"] == "marked_sold"
    assert (record["previous_crawl"], record["detected_in_crawl"]) == (D1, D2)
    assert record["data"]["is_sold"] is False
    assert record["sold_data"]["is_sold"] is True


def test_a_much_smaller_crawl_records_only_sold_flips(store: EntityStore):
    publish(store, D1, ["a", "b", "c", "d"])
    publish(store, D2, ["a", "b"], sold={"a": True})

    result = detect_private_sales(store, "artworks", "saatchi", D1, D2)

    assert (result.status, result.missing, result.marked_sold, result.written) == (
        "guard_tripped",
        2,
        1,
        1,
    )
    assert {k: v["reason"] for k, v in sales(store).items()} == {"a": "marked_sold"}
    assert detect_private_sales(store, "artworks", "saatchi", D1, D2, min_ratio=0.5).written == 3


def test_dry_run_writes_nothing(store: EntityStore):
    publish(store, D1, ["a", "b", "c", "d", "e"])
    publish(store, D2, ["a", "b", "c", "d"])

    assert detect_private_sales(store, "artworks", "saatchi", D1, D2, dry_run=True).missing == 1
    assert sales(store) == {}
    assert "private_sales" not in store.get_crawl(crawl_ledger_id("artworks", "saatchi", D2))


def test_crawls_must_be_finished_and_in_order(store: EntityStore):
    publish(store, D1, ["a"])
    with pytest.raises(ValueError, match="no completed ETL run"):
        detect_private_sales(store, "artworks", "saatchi", D1, D2)
    publish(store, D2, ["a"])
    with pytest.raises(ValueError, match="must be before"):
        detect_private_sales(store, "artworks", "saatchi", D2, D1)


def test_pending_diffs_consecutive_full_crawls_once(store: EntityStore):
    full = {"crawl_mode": "full", "crawl_status": "completed"}
    publish(store, D1, ["a", "b", "c", "d", "e"], **full)
    publish(store, "2026-10-12", ["x"], crawl_mode="incremental", crawl_status="completed")
    publish(store, D2, ["a", "b", "c", "d"], **full)
    publish(store, D3, ["a", "b", "c", "d", "g"], crawl_mode="full", crawl_status="failed")

    results = detect_pending_private_sales(store, "artworks", "saatchi")

    assert [(r.previous, r.current, r.missing) for r in results] == [(D1, D2, 1)]
    assert list(sales(store)) == ["e"]
    assert detect_pending_private_sales(store, "artworks", "saatchi") == []


def test_artworks_gone_with_their_artist_are_not_private_sales(store: EntityStore):
    others = [f"o{n}" for n in range(20)]  # keeps the crawl above the 80% guard
    artists = {"a": "x", "b": "x", "c": "y", "d": "y", "e": "z"}
    publish(store, D1, ["a", "b", "c", "d", "e", *others], artists=artists)
    # x lost one of two works; y lost both, so y left; z left with its only work.
    publish(store, D2, ["a", *others], artists=artists)

    result = detect_private_sales(store, "artworks", "saatchi", D1, D2)

    assert (result.missing, result.artist_removed, result.removed_artists) == (4, 3, 2)
    assert result.profiles_checked is False  # no artists ETL for these crawls
    assert set(sales(store)) == {"b"}


def test_artworks_of_a_deleted_artist_profile_are_not_private_sales(store: EntityStore):
    others = [f"o{n}" for n in range(20)]
    artists = {"a": "x", "b": "x", "c": "y", "d": "y"}
    publish(store, D1, ["a", "b", "c", "d", *others], artists=artists)
    publish(store, D2, ["a", "c", *others], artists=artists)
    publish_artists(store, D1, ["x", "y"])
    publish_artists(store, D2, ["y"])  # x deleted the profile; a is a leftover card

    result = detect_private_sales(store, "artworks", "saatchi", D1, D2)

    assert result.profiles_checked is True
    assert (result.missing, result.artist_removed) == (2, 1)
    assert set(sales(store)) == {"d"}


def test_pending_waits_for_the_artists_etl(store: EntityStore):
    full = {"crawl_mode": "full", "crawl_status": "completed"}
    publish(store, D1, ["a", "b", "c", "d", "e"], **full)
    publish(store, D2, ["a", "b", "c", "d"], **full)
    publish_artists(store, D1, [])

    assert detect_pending_private_sales(store, "artworks", "saatchi") == []
    assert sales(store) == {}

    publish_artists(store, D2, [])
    [result] = detect_pending_private_sales(store, "artworks", "saatchi")
    assert (result.profiles_checked, result.written) == (True, 1)


def _artwork_crawl(data_root, crawl_date: str, artwork_ids: list[int], crawl_mode: str):
    page = saatchi_artist(artworks=[saatchi_card(artwork_id=i) for i in artwork_ids])
    data = make_crawl(data_root / "saatchi", crawl_date, {"a.html": page})
    write_crawl_manifest(data, crawl_mode=crawl_mode)


@pytest.mark.parametrize(("crawl_mode", "expected"), [("full", 1), ("incremental", 0)])
def test_sync_records_private_sales_between_full_crawls(
    store: EntityStore, mongo_config: MongoConfig, tmp_path, crawl_mode: str, expected: int
):
    data_root = tmp_path / "data"
    _artwork_crawl(data_root, D1, [1, 2, 3, 4, 5], crawl_mode)
    _artwork_crawl(data_root, D2, [1, 2, 3, 4], crawl_mode)

    result = sync(ARTWORKS, SyncConfig(data_root=data_root, mongo=mongo_config, workers=1))

    assert result.ok
    assert len(sales(store)) == expected


def test_cli_private_sales(store: EntityStore, mongo_config: MongoConfig):
    publish(store, D1, ["a", "b", "c", "d", "e"])
    publish(store, D2, ["a", "b", "c", "d"])
    args = ["--mongo-uri", mongo_config.uri, "--mongo-db", mongo_config.database]

    argv = ["private-sales", "--platform", "saatchi", "--previous", D1, "--current", D2, *args]
    assert artwork_main(argv) == 0
    assert list(sales(store)) == ["e"]
