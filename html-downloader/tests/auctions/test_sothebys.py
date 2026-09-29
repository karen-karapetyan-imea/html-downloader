from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from html_downloader.auctions.registry import fetch_auction_entries, get_auction
from html_downloader.auctions.sitemap_cache import append_typed_cache
from html_downloader.auctions.sothebys import (
    fetch_sothebys_entries,
    known_sothebys_keys_from_paths,
    legacy_departments_for,
    lot_cache_path,
    site_search_departments_for,
)
from html_downloader.auctions.sothebys_algolia import (
    ART_FACETS,
    AUCTIONS_INDEX,
    build_art_filter,
)
from html_downloader.auctions.sothebys_legacy import LEGACY_ART_DEPARTMENTS, RESULTS_URL
from html_downloader.auctions.sothebys_search import FULL_TYPE_FILTER
from html_downloader.discover.sitemap import SitemapEntry

AUCTION_HIT = {
    "objectID": "aaaa-1",
    "slugYear": "2025",
    "slugName": "old-masters",
    "state": "Closed",
    "auctionDates": {"endDate": 1764806400},
}
LOT_HIT = {"objectID": "l1", "lotNr": 1, "slug": "/en/buy/auction/2025/old-masters/a-lot"}


class FakeClient:
    """Stands in for SothebysAlgoliaClient: only ``query`` is used."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, index: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((index, params))
        if index == AUCTIONS_INDEX:
            hits = [AUCTION_HIT] if 'slugYear:"2025"' in params["filters"] else []
        else:
            hits = [LOT_HIT]
        return {"nbHits": len(hits), "hits": hits}

    def close(self) -> None:
        raise AssertionError("caller-owned client must not be closed")


def test_full_mode_includes_sale_pages(tmp_path: Path) -> None:
    client = FakeClient()
    entries = fetch_sothebys_entries(
        state_path=tmp_path / "sothebys_algolia_browse_state.json",
        client=client,  # type: ignore[arg-type]
    )
    assert {e.entity_key for e in entries} == {
        ("sale", "2025/old-masters"),
        ("lot", "2025/old-masters/a-lot"),
    }
    lot_filters = [p["filters"] for i, p in client.calls if i != AUCTIONS_INDEX]
    assert lot_filters == ['auctionId:"aaaa-1" AND isTestLot:false']


def test_art_mode_filters_facets_and_skips_sales(tmp_path: Path) -> None:
    client = FakeClient()
    state_path = tmp_path / "sothebys_algolia_artworks_browse_state.json"
    entries = fetch_sothebys_entries(
        state_path=state_path,
        art_categories=["Prints"],
        client=client,  # type: ignore[arg-type]
    )
    assert [e.entity_key for e in entries] == [("lot", "2025/old-masters/a-lot")]
    lot_filters = [p["filters"] for i, p in client.calls if i != AUCTIONS_INDEX]
    assert lot_filters == [
        f'auctionId:"aaaa-1" AND isTestLot:false AND {build_art_filter(["Prints"])}'
    ]
    assert lot_cache_path(state_path).is_file()


class FakeLegacy:
    """Stands in for LegacyHtmlClient: one results page, one two-lot sale."""

    SALE = "https://www.sothebys.com/en/auctions/2008/indian-art-n08417.html"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str) -> str | None:
        self.calls.append(url)
        if url.startswith(RESULTS_URL):
            return f'{_DEPARTMENT_FORM}"{self.SALE}"<span data-page-count>1</span>'
        if url == self.SALE:
            return (
                '<div class="AuctionsModule-lotsCount"> 2 lots</div>'
                '"/en/auctions/ecatalogue/2008/indian-art-n08417/lot.1.html"'
                '"/en/auctions/ecatalogue/2008/indian-art-n08417/lot.2.html"'
            )
        return None


_DEPARTMENT_FORM = '<input name="f2" value="id-prints"><label for="id-prints">Prints</label>'


def test_full_mode_runs_legacy_pass_into_its_own_cache(tmp_path: Path) -> None:
    legacy_state = tmp_path / "sothebys_legacy_state.json"
    legacy = FakeLegacy()
    entries = fetch_sothebys_entries(
        state_path=tmp_path / "sothebys_algolia_browse_state.json",
        client=FakeClient(),  # type: ignore[arg-type]
        legacy_state_path=legacy_state,
        legacy_client=legacy,  # type: ignore[arg-type]
    )
    assert {e.entity_key for e in entries} == {
        ("sale", "2025/old-masters"),
        ("lot", "2025/old-masters/a-lot"),
        ("sale", "legacy/2008/indian-art-n08417"),
        ("lot", "legacy/2008/indian-art-n08417/1"),
        ("lot", "legacy/2008/indian-art-n08417/2"),
    }
    assert legacy.calls[0] == RESULTS_URL
    assert len(lot_cache_path(legacy_state).read_text(encoding="utf-8").splitlines()) == 3


def test_art_mode_filters_legacy_listing_by_department(tmp_path: Path) -> None:
    legacy = FakeLegacy()
    entries = fetch_sothebys_entries(
        state_path=tmp_path / "sothebys_algolia_artworks_browse_state.json",
        art_categories=["Prints"],
        client=FakeClient(),  # type: ignore[arg-type]
        legacy_state_path=tmp_path / "sothebys_legacy_artworks_state.json",
        legacy_client=legacy,  # type: ignore[arg-type]
    )
    assert f"{RESULTS_URL}?f2=id-prints" in legacy.calls
    assert ("lot", "legacy/2008/indian-art-n08417/2") in {e.entity_key for e in entries}


OLD_LOT_URL = "https://www.sothebys.com/en/auctions/ecatalogue/2003/prints-n07888/lot.513.html"


class FakeSiteSearch:
    """Stands in for the site-search Algolia client: every window returns the same two hits."""

    def __init__(self) -> None:
        self.filters: list[str] = []

    def query(self, index: str, params: dict[str, Any]) -> dict[str, Any]:
        self.filters.append(params["filters"])
        hits = [
            {"url": "https://www.sothebys.com/en/buy/auction/2025/old-masters/a-lot"},
            {"url": OLD_LOT_URL, "endDate": 1068595200000},
        ]
        return {"nbHits": len(hits), "hits": hits if params.get("hitsPerPage") else []}

    def close(self) -> None:
        raise AssertionError("caller-owned client must not be closed")


def _cache_ids(path: Path) -> list[str]:
    return [json.loads(line)["entity_id"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_site_search_pass_only_adds_keys_other_sources_lack(tmp_path: Path) -> None:
    algolia_state = tmp_path / "sothebys_algolia_browse_state.json"
    site_state = tmp_path / "sothebys_site_search_state.json"
    site = FakeSiteSearch()
    entries = fetch_sothebys_entries(
        state_path=algolia_state,
        client=FakeClient(),  # type: ignore[arg-type]
        site_search_state_path=site_state,
        site_search_client=site,  # type: ignore[arg-type]
    )
    assert ("lot", "legacy/2003/prints-n07888/513") in {e.entity_key for e in entries}
    assert _cache_ids(lot_cache_path(site_state)) == ["legacy/2003/prints-n07888/513"]
    assert _cache_ids(lot_cache_path(algolia_state)) == [
        "2025/old-masters",
        "2025/old-masters/a-lot",
    ]
    assert all(f.startswith(FULL_TYPE_FILTER) for f in site.filters)


def test_algolia_pass_skips_keys_already_in_site_search_cache(tmp_path: Path) -> None:
    algolia_state = tmp_path / "sothebys_algolia_browse_state.json"
    site_state = tmp_path / "sothebys_site_search_state.json"
    append_typed_cache(
        lot_cache_path(site_state),
        [
            SitemapEntry(
                url="https://www.sothebys.com/en/buy/auction/2025/old-masters/a-lot",
                lastmod=None,
                entity_type="lot",
                entity_id="2025/old-masters/a-lot",
            )
        ],
    )
    fetch_sothebys_entries(
        state_path=algolia_state,
        client=FakeClient(),  # type: ignore[arg-type]
        site_search_state_path=site_state,
        site_search_client=FakeSiteSearch(),  # type: ignore[arg-type]
    )
    assert _cache_ids(lot_cache_path(algolia_state)) == ["2025/old-masters"]


def test_site_search_departments_for_modes() -> None:
    assert site_search_departments_for(None) is None
    art = site_search_departments_for(list(ART_FACETS))
    assert art is not None
    assert set(art) == set(LEGACY_ART_DEPARTMENTS) | set(ART_FACETS)
    assert site_search_departments_for(["Prints"]) == ["Prints"]


def test_legacy_departments_for_modes() -> None:
    assert legacy_departments_for(None) is None
    assert legacy_departments_for(list(ART_FACETS)) == list(LEGACY_ART_DEPARTMENTS)
    assert legacy_departments_for(["Prints", "Prints", "Photographs"]) == ["Prints", "Photographs"]


def test_known_keys_from_prior_results(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    rows = [
        {"url": "https://www.sothebys.com/en/buy/auction/2025/old-masters/a-lot/"},
        {"url": "https://www.sothebys.com/fr/buy/auction/2025/old-masters"},
        {"url": "https://www.sothebys.com/en/articles/story"},
    ]
    results.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert known_sothebys_keys_from_paths([results]) == {
        ("lot", "2025/old-masters/a-lot"),
        ("sale", "2025/old-masters"),
    }


def test_registry_requires_state_path() -> None:
    spec = get_auction("sothebys")
    assert spec.uses_stealth_proxy is False
    with pytest.raises(ValueError):
        fetch_auction_entries(spec, concurrency=1, algolia_state_path=None)
