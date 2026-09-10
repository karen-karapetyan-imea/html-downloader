"""Tests for Invaluable Algolia archive browse discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from html_downloader.auctions.invaluable import _merge_entry
from html_downloader.auctions.invaluable_algolia import (
    AlgoliaBrowseState,
    build_lot_url,
    expand_lots_from_algolia,
    hit_to_sitemap_entry,
    hits_to_sitemap_entries,
    slugify,
    year_filter,
)
from html_downloader.discover.sitemap import SitemapEntry


def test_slugify_and_build_lot_url() -> None:
    assert slugify("Sunset Over Water!") == "sunset-over-water"
    url = build_lot_url("Sunset Over Water!", "12", "AaBbCcDdEe")
    assert url == (
        "https://www.invaluable.com/auction-lot/sunset-over-water-12-c-aabbccddee"
    )


def test_year_filter_bounds() -> None:
    # 2024-01-01 UTC .. 2025-01-01 UTC
    assert year_filter(2024) == (
        "dateTimeUTCUnix>=1704067200 AND dateTimeUTCUnix<1735689600"
    )


def test_hit_to_sitemap_entry_ok() -> None:
    hit = {
        "lotRef": "AaBbCcDdEe",
        "lotTitle": "Blue Study",
        "lotNumber": 7,
        "dateTimeLocal": "2024-06-15T10:00:00",
    }
    entry = hit_to_sitemap_entry(hit)
    assert entry is not None
    assert entry.entity_type == "lot"
    assert entry.entity_id == "aabbccddee"
    assert entry.lastmod == "2024-06-15"
    assert entry.url.endswith("-7-c-aabbccddee")


def test_hit_to_sitemap_entry_skips_banned_and_missing_ref() -> None:
    assert hit_to_sitemap_entry({"banned": True, "lotRef": "aaaaaaaaaa"}) is None
    assert hit_to_sitemap_entry({"lotTitle": "x"}) is None


def test_hits_to_sitemap_entries_filters() -> None:
    hits = [
        {"lotRef": "aaaaaaaaaa", "lotTitle": "A", "lotNumber": 1, "dateTimeLocal": "2020-01-01"},
        {"banned": True, "lotRef": "bbbbbbbbbb"},
        {"lotTitle": "no ref"},
    ]
    entries = hits_to_sitemap_entries(hits)
    assert len(entries) == 1
    assert entries[0].entity_id == "aaaaaaaaaa"


def test_merge_dedupes_algolia_vs_xml() -> None:
    best: dict[tuple[str, str], SitemapEntry] = {}
    xml = SitemapEntry(
        url="https://www.invaluable.com/auction-lot/old-title-1-c-aaaaaaaaaa",
        lastmod="2024-01-01",
        entity_type="lot",
        entity_id="aaaaaaaaaa",
    )
    _merge_entry(best, xml)
    algolia = SitemapEntry(
        url="https://www.invaluable.com/auction-lot/new-title-1-c-aaaaaaaaaa",
        lastmod="2024-06-01",
        entity_type="lot",
        entity_id="aaaaaaaaaa",
    )
    _merge_entry(best, algolia)
    assert len(best) == 1
    kept = best[("lot", "aaaaaaaaaa")]
    assert kept.lastmod == "2024-06-01"
    assert kept.entity_id == "aaaaaaaaaa"
    # Both URLs are /auction-lot/ form — prefer_entity_url keeps existing on score tie
    assert "-c-aaaaaaaaaa" in kept.url


def test_expand_lots_cursor_walk_and_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"
    pages: dict[str | None, dict[str, Any]] = {
        None: {
            "hits": [
                {
                    "lotRef": "aaaaaaaaaa",
                    "lotTitle": "One",
                    "lotNumber": 1,
                    "dateTimeLocal": "2024-01-02",
                }
            ],
            "cursor": "c1",
        },
        "c1": {
            "hits": [
                {
                    "lotRef": "bbbbbbbbbb",
                    "lotTitle": "Two",
                    "lotNumber": 2,
                    "dateTimeLocal": "2024-02-03",
                }
            ],
            # no cursor → end
        },
    }

    def browse(filters: str, cursor: str | None) -> dict[str, Any] | None:
        assert "dateTimeUTCUnix" in filters
        return pages[cursor]

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2024,
        to_year=2024,
        workers=1,
        delay=0.0,
        browse=browse,
    )
    ids = {e.entity_id for e in entries}
    assert ids == {"aaaaaaaaaa", "bbbbbbbbbb"}
    state = AlgoliaBrowseState(state_path)
    assert state.status("2024") == "done"

    # Second run: partitions done → emit from cache, no re-browse
    calls: list[str | None] = []

    def browse_again(filters: str, cursor: str | None) -> dict[str, Any] | None:
        calls.append(cursor)
        raise AssertionError("should not browse when partitions are done")

    cached = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2024,
        to_year=2024,
        workers=1,
        delay=0.0,
        browse=browse_again,
    )
    assert calls == []
    assert {e.entity_id for e in cached} == ids


def test_expand_lots_invalid_cursor_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"
    calls: list[str | None] = []

    def browse(filters: str, cursor: str | None) -> dict[str, Any] | None:
        calls.append(cursor)
        if len(calls) == 1:
            return {"_invalid_cursor": True}
        return {
            "hits": [
                {
                    "lotRef": "cccccccccc",
                    "lotTitle": "Restarted",
                    "lotNumber": 3,
                    "dateTimeLocal": "2023-05-01",
                }
            ],
        }

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2023,
        to_year=2023,
        workers=1,
        delay=0.0,
        browse=browse,
    )
    assert calls == [None, None]
    assert len(entries) == 1
    assert entries[0].entity_id == "cccccccccc"
    assert AlgoliaBrowseState(state_path).status("2023") == "done"


def test_algolia_force_clears_cache(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"

    def browse_a(filters: str, cursor: str | None) -> dict[str, Any] | None:
        return {
            "hits": [
                {
                    "lotRef": "aaaaaaaaaa",
                    "lotTitle": "A",
                    "lotNumber": 1,
                    "dateTimeLocal": "2022-01-01",
                }
            ],
        }

    expand_lots_from_algolia(
        state_path=state_path,
        from_year=2022,
        to_year=2022,
        workers=1,
        delay=0.0,
        browse=browse_a,
    )

    def browse_b(filters: str, cursor: str | None) -> dict[str, Any] | None:
        return {
            "hits": [
                {
                    "lotRef": "bbbbbbbbbb",
                    "lotTitle": "B",
                    "lotNumber": 2,
                    "dateTimeLocal": "2022-02-01",
                }
            ],
        }

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2022,
        to_year=2022,
        workers=1,
        delay=0.0,
        force=True,
        browse=browse_b,
    )
    assert {e.entity_id for e in entries} == {"bbbbbbbbbb"}
