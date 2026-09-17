"""Tests for The Saleroom Algolia-first lot discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from html_downloader.auctions.saleroom import fetch_saleroom_sitemap_entries
from html_downloader.auctions.saleroom_algolia import (
    ART_MASTER_CATEGORY_CODES,
    expand_lots_from_algolia,
    hit_to_lot_url,
    hit_to_sitemap_entry,
    load_lot_cache_urls,
    lot_cache_path,
    master_category_filter,
    plan_partitions,
)
LOT_UUID = "003bc9c2-31b8-4bbf-bb91-b4bc01078797"
LOT_URL = (
    "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
    f"catalogue-id-kew-au10015/lot-{LOT_UUID}"
)


def _hit(
    *,
    object_id: str = LOT_UUID,
    auctioneer_ref: str = "kew",
    auction_ref: str = "kew-au10015",
) -> dict[str, Any]:
    return {
        "objectID": object_id,
        "auctioneerRef": auctioneer_ref,
        "auctionRef": auction_ref,
    }


def test_hit_to_lot_url() -> None:
    assert hit_to_lot_url(_hit()) == LOT_URL
    assert hit_to_lot_url({"objectID": "x"}) is None
    entry = hit_to_sitemap_entry(_hit())
    assert entry is not None
    assert entry.entity_type == "lot"
    assert entry.entity_id == LOT_UUID
    assert entry.url == LOT_URL


def test_master_category_filter() -> None:
    assert master_category_filter("fia") == "masterCategoryCode:FIA"
    assert set(ART_MASTER_CATEGORY_CODES) == {"FIA", "DEA", "AA", "ETA", "COL", "GRE"}


def test_plan_partitions_single_and_country_split() -> None:
    calls: list[str] = []

    def fake_query(filters: str, page: int, hits_per_page: int) -> dict[str, Any]:
        _ = (page, hits_per_page)
        calls.append(filters)
        if filters == "masterCategoryCode:FIA":
            return {"nbHits": 10, "hits": [], "nbPages": 0}
        if filters == "masterCategoryCode:COL":
            return {
                "nbHits": 100_000,
                "hits": [],
                "nbPages": 0,
                "facets": {"countryName": {"United Kingdom": 80_000, "France": 20_000}},
            }
        if filters == 'masterCategoryCode:COL AND countryName:"United Kingdom"':
            return {
                "nbHits": 80_000,
                "hits": [],
                "nbPages": 0,
                "facets": {"auctioneerName": {"House A": 50_000, "House B": 30_000}},
            }
        if filters == 'masterCategoryCode:COL AND countryName:"France"':
            return {"nbHits": 20_000, "hits": [], "nbPages": 0}
        return {"nbHits": 0, "hits": [], "nbPages": 0}

    planned = plan_partitions(
        ("FIA", "COL"),
        query=fake_query,
        max_pages=2,
        hits_per_page=100,
    )
    assert ("FIA", "masterCategoryCode:FIA") in planned
    keys = {key for key, _filters in planned}
    assert "COL|France" in keys
    assert "COL|United Kingdom|House A" in keys
    assert "COL|United Kingdom|House B" in keys
    assert "COL|United Kingdom" not in keys
    assert "COL" not in keys


def test_expand_lots_from_algolia_mocked(tmp_path: Path) -> None:
    state_path = tmp_path / "saleroom_algolia_browse_state.json"
    hit_a = _hit()
    hit_b = _hit(
        object_id="00da9463-88ae-43b0-bb0a-b4bc010787a0",
        auctioneer_ref="kew",
        auction_ref="kew-au10015",
    )
    pages: dict[tuple[str, int], dict[str, Any]] = {
        ("masterCategoryCode:FIA", 0): {
            "nbHits": 2,
            "nbPages": 1,
            "hits": [hit_a, hit_b],
        },
    }

    def fake_query(filters: str, page: int, hits_per_page: int) -> dict[str, Any]:
        _ = hits_per_page
        if page == 0 and filters.startswith("masterCategoryCode:"):
            # plan_partitions probe
            if (filters, 0) not in pages and "AND" not in filters:
                code = filters.split(":", 1)[1]
                if code == "FIA":
                    return {"nbHits": 2, "nbPages": 1, "hits": []}
                return {"nbHits": 0, "nbPages": 0, "hits": []}
        return pages.get((filters, page), {"nbHits": 0, "nbPages": 0, "hits": []})

    entries = expand_lots_from_algolia(
        state_path=state_path,
        master_codes=("FIA", "DEA"),
        query=fake_query,
        delay=0,
        hits_per_page=100,
    )
    assert len(entries) == 2
    cache = lot_cache_path(state_path)
    urls = load_lot_cache_urls(cache)
    assert LOT_URL in urls
    assert state_path.is_file()

    # Resume: partitions done → no new entries
    again = expand_lots_from_algolia(
        state_path=state_path,
        master_codes=("FIA", "DEA"),
        query=fake_query,
        delay=0,
    )
    assert again == []


def test_fetch_saleroom_sitemap_entries_algolia_only(tmp_path: Path) -> None:
    state_path = tmp_path / "saleroom_algolia_browse_state.json"
    sample = [
        hit_to_sitemap_entry(
            _hit(
                object_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                auctioneer_ref="house",
                auction_ref="cat1",
            )
        )
    ]
    assert sample[0] is not None

    from unittest.mock import patch

    with patch(
        "html_downloader.auctions.saleroom.expand_lots_from_algolia",
        return_value=sample,
    ) as expand:
        entries = fetch_saleroom_sitemap_entries(
            expand_algolia=True,
            algolia_state_path=state_path,
            algolia_delay=0.1,
            algolia_force=True,
        )
    assert entries == sample
    expand.assert_called_once()
    kwargs = expand.call_args.kwargs
    assert kwargs["state_path"] == state_path
    assert kwargs["force"] is True
    assert kwargs["delay"] == 0.1


def test_fetch_requires_state_path() -> None:
    import pytest

    with pytest.raises(ValueError, match="algolia_state_path"):
        fetch_saleroom_sitemap_entries(expand_algolia=True, algolia_state_path=None)
