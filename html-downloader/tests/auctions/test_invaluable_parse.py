from __future__ import annotations

from pathlib import Path

from html_downloader.auctions.invaluable import (
    is_invaluable_hub_sitemap,
    is_invaluable_target_sitemap,
    parse_invaluable_entries,
    save_auction_lastmod_state,
)
from html_downloader.auctions.paths import auction_lastmod_state_file
from html_downloader.discover.sitemap import load_lastmod_state

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_sitemap_filters_lots_and_catalogs() -> None:
    xml = (FIXTURES / "catalog_sitemap.xml").read_bytes()
    entries = parse_invaluable_entries(xml)
    by_key = {e.entity_key: e for e in entries}
    assert ("catalog", "01bmdj3kwy") in by_key
    assert ("catalog", "0ak1fxhm3a") in by_key
    assert ("catalog", "128kl0n32e") in by_key
    assert ("lot", "f38e67d2a8") in by_key
    assert ("lot", "bfa8908388") in by_key
    assert ("house", "9mq71klbbn") in by_key
    assert by_key[("catalog", "01bmdj3kwy")].lastmod == "2026-09-02"
    assert by_key[("lot", "f38e67d2a8")].lastmod == "2026-09-06"
    urls = "\n".join(e.url for e in entries)
    assert "advancedSearch" not in urls
    assert "/artists/" not in urls
    assert "/auction-lot/" in urls
    assert "/auction-house/" in urls


def test_parse_relative_catalog_with_base_injection() -> None:
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.invaluable.com/catalog/0ak1fxhm3a</loc><lastmod>2026-09-03</lastmod></url>
    </urlset>
    """
    entries = parse_invaluable_entries(xml)
    assert len(entries) == 1
    assert entries[0].url == "https://www.invaluable.com/catalog/0ak1fxhm3a"


def test_parse_hub_and_category_entries() -> None:
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.invaluable.com/artist/dali-salvador-9chkguv69j</loc></url>
      <url><loc>https://www.invaluable.com/fine-art/pc-SG2BIX3JPJ/</loc></url>
      <url><loc>https://www.invaluable.com/artist/x-1/sold-at-auction-prices/</loc></url>
    </urlset>
    """
    entries = parse_invaluable_entries(xml)
    keys = {e.entity_key for e in entries}
    assert keys == {("artist", "9chkguv69j"), ("category", "pc-sg2bix3jpj")}


def test_target_sitemap_filter() -> None:
    assert is_invaluable_target_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-lot-2026-9-pt1.xml"
    )
    assert is_invaluable_target_sitemap(
        "https://www.invaluable.com/past_search_sitemap.xml"
    )
    assert is_invaluable_target_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-catalog.xml"
    )
    assert not is_invaluable_target_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-artists.xml"
    )
    assert not is_invaluable_target_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-auctionhouse.xml"
    )
    assert is_invaluable_hub_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-artists.xml"
    )
    assert is_invaluable_hub_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-auctionhouse.xml"
    )
    assert is_invaluable_hub_sitemap(
        "https://www.invaluable.com/sitemap_inv_com-category.xml"
    )


def test_atomic_state_write(tmp_path: Path) -> None:
    path = auction_lastmod_state_file(tmp_path, "invaluable")
    save_auction_lastmod_state(path, {"lot:f38e67d2a8": "2026-09-06"})
    loaded = load_lastmod_state(path)
    assert loaded["lot:f38e67d2a8"] == "2026-09-06"
    assert not list(path.parent.glob("*.tmp"))
