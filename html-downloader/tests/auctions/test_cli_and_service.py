from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from html_downloader.auctions.service import run_auction_discover
from html_downloader.cli import build_parser, main
from html_downloader.discover.sitemap import SitemapEntry


def _entry(url: str, entity_type: str, entity_id: str, lastmod: str) -> SitemapEntry:
    return SitemapEntry(
        url=url,
        lastmod=lastmod,
        entity_type=entity_type,
        entity_id=entity_id,
    )


def test_cli_auction_discover_and_download_flags() -> None:
    parser = build_parser()
    discover_args = parser.parse_args(
        [
            "auction",
            "discover",
            "--auction-house",
            "invaluable",
            "--month",
            "2026-09",
            "--incremental",
            "--update-state",
            "--no-expand-algolia",
            "--expand-artist-sold",
            "--max-artist-sold",
            "10",
            "--no-include-hubs",
            "--no-expand-auctions-list",
            "--expand-houses",
            "--max-houses",
            "5",
            "--algolia-from-year",
            "2020",
            "--algolia-workers",
            "3",
        ]
    )
    assert discover_args.command == "auction"
    assert discover_args.auction_command == "discover"
    assert discover_args.auction_house == "invaluable"
    assert discover_args.month == "2026-09"
    assert discover_args.expand_algolia is False
    assert discover_args.expand_artist_sold is True
    assert discover_args.max_artist_sold == 10
    assert discover_args.include_hubs is False
    assert discover_args.expand_auctions_list is False
    assert discover_args.expand_houses is True
    assert discover_args.max_houses == 5
    assert discover_args.algolia_from_year == 2020
    assert discover_args.algolia_workers == 3
    assert discover_args.algolia_artworks_only is False
    assert discover_args.algolia_supercategories is None

    artworks_args = parser.parse_args(
        [
            "auction",
            "discover",
            "--auction-house",
            "invaluable",
            "--algolia-artworks-only",
            "--algolia-supercategories",
            "Decorative Art",
        ]
    )
    assert artworks_args.algolia_artworks_only is True
    assert artworks_args.algolia_supercategories == ["Decorative Art"]

    la_args = parser.parse_args(
        [
            "auction",
            "discover",
            "--auction-house",
            "liveauctioneers",
            "--max-sitemaps",
            "25",
            "--min-urls",
            "50000",
            "--sitemap-force",
            "--incremental",
        ]
    )
    assert la_args.auction_house == "liveauctioneers"
    assert la_args.max_sitemaps == 25
    assert la_args.min_urls == 50000
    assert la_args.sitemap_force is True

    ac_args = parser.parse_args(
        [
            "auction",
            "discover",
            "--auction-house",
            "artcurial",
            "--max-sales",
            "5",
            "--incremental",
            "--update-state",
        ]
    )
    assert ac_args.auction_house == "artcurial"
    assert ac_args.max_sales == 5

    bm_args = parser.parse_args(
        [
            "auction",
            "discover",
            "--auction-house",
            "barnebys",
            "--max-sitemaps",
            "8",
            "--min-urls",
            "100000",
            "--sitemap-force",
            "--incremental",
        ]
    )
    assert bm_args.auction_house == "barnebys"
    assert bm_args.max_sitemaps == 8
    assert bm_args.min_urls == 100000
    assert bm_args.sitemap_force is True

    # Defaults: Algolia on, artist-sold off
    defaults = parser.parse_args(
        ["auction", "discover", "--auction-house", "invaluable"]
    )
    assert defaults.expand_algolia is True
    assert defaults.expand_artist_sold is False
    assert defaults.algolia_workers == 2
    assert defaults.algolia_delay == 0.4
    assert defaults.algolia_force is False
    assert defaults.max_sitemaps is None
    assert defaults.min_urls is None
    assert defaults.sitemap_force is False
    assert defaults.max_sales is None

    download_args = parser.parse_args(
        [
            "auction",
            "download",
            "--auction-house",
            "invaluable",
            "--proxy-file",
            "proxy.txt",
            "--skip-existing",
        ]
    )
    assert download_args.command == "auction"
    assert download_args.auction_command == "download"
    assert download_args.skip_existing is True


def test_cli_marketplace_commands_still_parse() -> None:
    parser = build_parser()
    args = parser.parse_args(["discover", "--marketplace", "saatchi"])
    assert args.command == "discover"
    assert args.marketplace == "saatchi"
    assert args.expand_search is True

    args = parser.parse_args(
        ["discover", "--marketplace", "saatchi", "--no-expand-search", "--search-force"]
    )
    assert args.expand_search is False
    assert args.search_force is True

    args = parser.parse_args(
        ["download", "--marketplace", "artsper", "--proxy-file", "proxy.txt"]
    )
    assert args.command == "download"
    assert args.marketplace == "artsper"


def test_auction_discover_writes_monthly_job(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.invaluable.com/auction-lot/foo-c-aaaaaaaaaa",
            "lot",
            "aaaaaaaaaa",
            "2026-09-01",
        ),
        _entry(
            "https://www.invaluable.com/catalog/0ak1fxhm3a",
            "catalog",
            "0ak1fxhm3a",
            "2026-09-02",
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="invaluable",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=str(proxy_file),
            concurrency=1,
            dry_run=False,
            expand_artist_sold=False,
            expand_algolia=False,
        )

    job = result.job
    assert job == data_root / "auctions" / "invaluable" / "2026-09"
    urls = (job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    all_urls = (job / "sitemap_all.txt").read_text(encoding="utf-8").strip().splitlines()
    assert len(urls) == 2
    assert urls == all_urls
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "invaluable"
    assert meta["job_month"] == "2026-09"
    assert (state_root / "auctions" / "invaluable.json").is_file()
    # Marketplace-style state file must not be created
    assert not (state_root / "invaluable_lastmod.json").exists()


def test_liveauctioneers_discover_writes_monthly_job(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.liveauctioneers.com/price-result/oil-painting-123",
            "price_result",
            "oil-painting-123",
            "2026-09-01",
        ),
        _entry(
            "https://www.liveauctioneers.com/price-result/bronze-sculpture-abc",
            "price_result",
            "bronze-sculpture-abc",
            "2026-09-02",
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    # Pre-seed durable cache with an older lot so the job streams cache ∪ batch.
    from html_downloader.auctions.liveauctioneers import append_lot_cache, lot_cache_path
    from html_downloader.auctions.paths import auction_sitemap_progress_file

    progress_path = auction_sitemap_progress_file(state_root, "liveauctioneers")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    append_lot_cache(
        lot_cache_path(progress_path),
        [
            _entry(
                "https://www.liveauctioneers.com/price-result/cached-lot-999",
                "price_result",
                "cached-lot-999",
                "2026-08-01",
            ),
            # Duplicate of this-run entry — must not double-count in stream.
            _entry(
                "https://www.liveauctioneers.com/price-result/oil-painting-123",
                "price_result",
                "oil-painting-123",
                "2026-09-01",
            ),
        ],
    )

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="liveauctioneers",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=str(proxy_file),
            concurrency=1,
            dry_run=False,
            max_sitemaps=10,
        )

    job = result.job
    assert job == data_root / "auctions" / "liveauctioneers" / "2026-09"
    urls = (job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert set(urls) == {
        "https://www.liveauctioneers.com/price-result/oil-painting-123",
        "https://www.liveauctioneers.com/price-result/bronze-sculpture-abc",
        "https://www.liveauctioneers.com/price-result/cached-lot-999",
    }
    assert result.all_count == 3
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "liveauctioneers"
    assert meta["url_count"] == 3
    assert (state_root / "auctions" / "liveauctioneers.json").is_file()


def test_barnebys_discover_writes_monthly_job(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.barnebys.com/auctions/lot/foo-AbC12De-1001",
            "lot",
            "1001",
            None,
        ),
        _entry(
            "https://www.barnebys.com/realized-prices/lot/foo-AbC12De-1001",
            "result_lot",
            "1001",
            None,
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    from html_downloader.auctions.barnebys_algolia import append_lot_cache, lot_cache_path
    from html_downloader.auctions.paths import auction_sitemap_progress_file

    progress_path = auction_sitemap_progress_file(state_root, "barnebys")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    append_lot_cache(
        lot_cache_path(progress_path),
        [
            _entry(
                "https://www.barnebys.com/auctions/lot/cached-XyZ99Ab-2002",
                "lot",
                "2002",
                None,
            ),
            _entry(
                "https://www.barnebys.com/auctions/lot/foo-AbC12De-1001",
                "lot",
                "1001",
                None,
            ),
        ],
    )

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="barnebys",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=str(proxy_file),
            concurrency=1,
            dry_run=False,
            max_sitemaps=8,
        )

    job = result.job
    assert job == data_root / "auctions" / "barnebys" / "2026-09"
    urls = set((job / "urls.txt").read_text(encoding="utf-8").strip().splitlines())
    assert "https://www.barnebys.com/auctions/lot/foo-AbC12De-1001" in urls
    assert "https://www.barnebys.com/realized-prices/lot/foo-AbC12De-1001" in urls
    assert "https://www.barnebys.com/auctions/lot/cached-XyZ99Ab-2002" in urls
    assert result.all_count == 3
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "barnebys"
    assert (state_root / "auctions" / "barnebys.json").is_file()


def test_artcurial_discover_writes_monthly_job(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.artcurial.com/en/sales/6641/lots/1-a",
            "lot",
            "6641:1-a",
            "2026-09-08",
        ),
        _entry(
            "https://www.artcurial.com/en/sales/6641/lots/2-b",
            "lot",
            "6641:2-b",
            "2026-09-08",
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="artcurial",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=None,
            concurrency=4,
            dry_run=False,
            max_sales=5,
        )

    job = result.job
    assert job == data_root / "auctions" / "artcurial" / "2026-09"
    urls = (job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert len(urls) == 2
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "artcurial"
    assert (state_root / "auctions" / "artcurial.json").is_file()


def test_liveauctioneers_download_configures_bot_ua(tmp_path: Path) -> None:
    from html_downloader.auctions.service import run_auction_download

    data_root = tmp_path / "data"
    job = data_root / "auctions" / "liveauctioneers" / "2026-09"
    job.mkdir(parents=True)
    (job / "urls.txt").write_text(
        "https://www.liveauctioneers.com/price-result/oil-painting-123\n",
        encoding="utf-8",
    )
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    captured: dict = {}

    def fake_crawl(urls: list[str], config: object) -> None:
        captured["config"] = config
        captured["urls"] = urls

    with patch("html_downloader.auctions.service.run_crawl", side_effect=fake_crawl):
        result = run_auction_download(
            auction_house="liveauctioneers",
            data_root=data_root,
            job_month="2026-09",
            proxy_file=str(proxy_file),
            urls_override=None,
            max_workers=2,
            requests_per_second=1.0,
            skip_existing=True,
            results_append=True,
        )

    assert result.status == "completed"
    cfg = captured["config"]
    assert cfg.impersonate == "none"
    assert cfg.require_window_data is True
    assert "Googlebot" in cfg.extra_headers["User-Agent"]
    assert "incapsula" in cfg.block_keywords


def test_saleroom_discover_writes_monthly_job(tmp_path: Path) -> None:
    lot_url = (
        "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
        "catalogue-id-kew-au10015/lot-003bc9c2-31b8-4bbf-bb91-b4bc01078797"
    )
    entries = [
        _entry(lot_url, "lot", "003bc9c2-31b8-4bbf-bb91-b4bc01078797", "2026-09-17"),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"

    from html_downloader.auctions.paths import auction_algolia_browse_state_file
    from html_downloader.auctions.saleroom_algolia import append_lot_cache, lot_cache_path

    algolia_state = auction_algolia_browse_state_file(state_root, "saleroom")
    algolia_state.parent.mkdir(parents=True, exist_ok=True)
    algolia_state.write_text('{"partitions": {}}\n', encoding="utf-8")
    append_lot_cache(
        lot_cache_path(algolia_state),
        [
            SitemapEntry(
                url=lot_url,
                lastmod=None,
                entity_type="lot",
                entity_id="003bc9c2-31b8-4bbf-bb91-b4bc01078797",
            ),
            SitemapEntry(
                url=(
                    "https://www.the-saleroom.com/en-gb/auction-catalogues/x/"
                    "catalogue-id-x1/lot-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
                ),
                lastmod=None,
                entity_type="lot",
                entity_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            ),
        ],
    )

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="saleroom",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=None,
            concurrency=1,
            dry_run=False,
        )

    job = result.job
    assert job == data_root / "auctions" / "saleroom" / "2026-09"
    urls = (job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert lot_url in urls
    assert (
        "https://www.the-saleroom.com/en-gb/auction-catalogues/x/"
        "catalogue-id-x1/lot-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ) in urls
    art_urls = set((job / "art_urls.txt").read_text(encoding="utf-8").strip().splitlines())
    assert lot_url in art_urls
    assert (
        "https://www.the-saleroom.com/en-gb/auction-catalogues/x/"
        "catalogue-id-x1/lot-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ) in art_urls
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "saleroom"
    assert meta["art_lots"] == 2
    assert meta["all_lots"] == 2
    assert (state_root / "auctions" / "saleroom.json").is_file()


def test_saleroom_download_adds_aws_waf_keywords(tmp_path: Path) -> None:
    from html_downloader.auctions.service import run_auction_download

    data_root = tmp_path / "data"
    job = data_root / "auctions" / "saleroom" / "2026-09"
    job.mkdir(parents=True)
    (job / "urls.txt").write_text(
        "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
        "catalogue-id-kew-au10015/lot-003bc9c2-31b8-4bbf-bb91-b4bc01078797\n",
        encoding="utf-8",
    )
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    captured: dict = {}

    def fake_crawl(urls: list[str], config: object) -> None:
        captured["config"] = config
        captured["urls"] = urls

    with patch("html_downloader.auctions.service.run_crawl", side_effect=fake_crawl):
        result = run_auction_download(
            auction_house="saleroom",
            data_root=data_root,
            job_month="2026-09",
            proxy_file=str(proxy_file),
            urls_override=None,
            max_workers=2,
            requests_per_second=1.0,
            skip_existing=True,
            results_append=True,
        )

    assert result.status == "completed"
    cfg = captured["config"]
    assert "awswaf" in cfg.block_keywords
    assert "human verification" in cfg.block_keywords


def test_drouot_discover_writes_monthly_job(tmp_path: Path) -> None:
    lot_url = "https://drouot.com/en/l/34624465-tiffany-heart"
    sale_url = "https://drouot.com/en/v/184832-japanese-crafts"
    entries = [
        _entry(lot_url, "lot", "34624465", "2026-09-17"),
        _entry(sale_url, "sale", "184832", "2026-09-17"),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"

    from html_downloader.auctions.drouot import append_lot_cache, lot_cache_path
    from html_downloader.auctions.paths import auction_sitemap_progress_file

    progress_path = auction_sitemap_progress_file(state_root, "drouot")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    append_lot_cache(
        lot_cache_path(progress_path),
        [
            SitemapEntry(
                url=lot_url,
                lastmod=None,
                entity_type="lot",
                entity_id="34624465",
            ),
            SitemapEntry(
                url="https://drouot.com/en/l/999-cached-lot",
                lastmod=None,
                entity_type="lot",
                entity_id="999",
            ),
            SitemapEntry(
                url=sale_url,
                lastmod=None,
                entity_type="sale",
                entity_id="184832",
            ),
        ],
    )

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=entries,
    ):
        result = run_auction_discover(
            auction_house="drouot",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=None,
            concurrency=1,
            dry_run=False,
            expand_algolia=False,
        )

    job = result.job
    assert job == data_root / "auctions" / "drouot" / "2026-09"
    urls = set((job / "urls.txt").read_text(encoding="utf-8").strip().splitlines())
    assert lot_url in urls
    assert sale_url in urls
    assert "https://drouot.com/en/l/999-cached-lot" in urls
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "drouot"
    assert (state_root / "auctions" / "drouot.json").is_file()


def test_drouot_download_adds_cloudflare_keywords(tmp_path: Path) -> None:
    from html_downloader.auctions.service import run_auction_download

    data_root = tmp_path / "data"
    job = data_root / "auctions" / "drouot" / "2026-09"
    job.mkdir(parents=True)
    (job / "urls.txt").write_text(
        "https://drouot.com/en/l/34624465-tiffany-heart\n",
        encoding="utf-8",
    )
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    captured: dict = {}

    def fake_crawl(urls: list[str], config: object) -> None:
        captured["config"] = config

    with patch("html_downloader.auctions.service.run_crawl", side_effect=fake_crawl):
        result = run_auction_download(
            auction_house="drouot",
            data_root=data_root,
            job_month="2026-09",
            proxy_file=str(proxy_file),
            urls_override=None,
            max_workers=2,
            requests_per_second=1.0,
            skip_existing=True,
            results_append=True,
        )

    assert result.status == "completed"
    cfg = captured["config"]
    assert "challenge-platform" in cfg.block_keywords


def _christies_lot(object_id: str) -> SitemapEntry:
    return SitemapEntry(
        url=f"https://www.christies.com/en/lot/lot-{object_id}",
        lastmod="2026-09-01",
        entity_type="lot",
        entity_id=object_id,
    )


def test_christies_full_discover_streams_sitemap_cache(tmp_path: Path) -> None:
    from html_downloader.auctions.christies import append_lot_cache, lot_cache_path
    from html_downloader.auctions.paths import auction_sitemap_progress_file

    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    progress_path = auction_sitemap_progress_file(state_root, "christies")
    sale = SitemapEntry(
        url="https://www.christies.com/en/auction/auction-24211-par",
        lastmod="2025-11-19",
        entity_type="sale",
        entity_id="24211-par",
    )
    append_lot_cache(
        lot_cache_path(progress_path),
        [_christies_lot("1"), _christies_lot("2"), sale],
    )
    captured: dict = {}

    def fake_fetch(spec: object, **kwargs: object) -> list[SitemapEntry]:
        captured.update(kwargs)
        return [_christies_lot("1")]

    with patch("html_downloader.auctions.service.fetch_auction_entries", side_effect=fake_fetch):
        result = run_auction_discover(
            auction_house="christies",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=True,
            proxy_file=None,
            concurrency=None,
            dry_run=False,
        )

    assert captured["sitemap_progress_path"] == progress_path
    assert captured["algolia_state_path"] is None
    assert captured["max_sitemaps"] == 200
    assert captured["min_urls"] == 0
    lines = (result.job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert sorted(lines) == sorted(
        [
            "https://www.christies.com/en/lot/lot-1",
            "https://www.christies.com/en/lot/lot-2",
            "https://www.christies.com/en/auction/auction-24211-par",
        ]
    )
    assert result.all_count == 3
    assert (state_root / "auctions" / "christies.json").is_file()


def test_christies_art_only_uses_separate_state_and_facets(tmp_path: Path) -> None:
    from html_downloader.auctions.christies import append_lot_cache, lot_cache_path
    from html_downloader.auctions.christies_search import ART_CATEGORY_FACETS
    from html_downloader.auctions.paths import (
        auction_algolia_artworks_browse_state_file,
        auction_sitemap_progress_file,
    )

    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    art_state = auction_algolia_artworks_browse_state_file(state_root, "christies")
    append_lot_cache(lot_cache_path(art_state), [_christies_lot("10"), _christies_lot("11")])
    # Full-archive cache must be ignored in art-only mode.
    full_state = auction_sitemap_progress_file(state_root, "christies")
    append_lot_cache(lot_cache_path(full_state), [_christies_lot("99")])

    prior = data_root / "auctions" / "christies" / "2026-08"
    prior.mkdir(parents=True)
    (prior / "results.jsonl").write_text(
        json.dumps({"url": "https://www.christies.com/en/lot/lot-10", "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_fetch(spec: object, **kwargs: object) -> list[SitemapEntry]:
        captured.update(kwargs)
        return []

    with patch("html_downloader.auctions.service.fetch_auction_entries", side_effect=fake_fetch):
        result = run_auction_discover(
            auction_house="christies",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=True,
            include_updates=False,
            update_state=False,
            proxy_file=None,
            concurrency=None,
            dry_run=False,
            algolia_artworks_only=True,
        )

    assert captured["algolia_state_path"] == art_state
    assert captured["sitemap_progress_path"] is None
    assert captured["algolia_supercategories"] == list(ART_CATEGORY_FACETS)
    all_urls = (result.job / "sitemap_all.txt").read_text(encoding="utf-8").split()
    crawl = (result.job / "urls.txt").read_text(encoding="utf-8").split()
    assert sorted(all_urls) == [
        "https://www.christies.com/en/lot/lot-10",
        "https://www.christies.com/en/lot/lot-11",
    ]
    assert crawl == ["https://www.christies.com/en/lot/lot-11"]


def test_christies_download_replaces_block_keywords(tmp_path: Path) -> None:
    from html_downloader.auctions.christies import AKAMAI_KEYWORDS
    from html_downloader.auctions.service import run_auction_download

    data_root = tmp_path / "data"
    job = data_root / "auctions" / "christies" / "2026-09"
    job.mkdir(parents=True)
    (job / "urls.txt").write_text("https://www.christies.com/en/lot/lot-1\n", encoding="utf-8")
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")
    captured: dict = {}

    def fake_crawl(urls: list[str], config: object) -> None:
        captured["config"] = config

    with patch("html_downloader.auctions.service.run_crawl", side_effect=fake_crawl):
        run_auction_download(
            auction_house="christies",
            data_root=data_root,
            job_month="2026-09",
            proxy_file=str(proxy_file),
            urls_override=None,
            max_workers=2,
            requests_per_second=1.0,
            skip_existing=True,
            results_append=True,
        )

    keywords = captured["config"].block_keywords
    assert keywords == AKAMAI_KEYWORDS
    assert "challenge" not in keywords
    assert "blocked" not in keywords


def test_main_help_exit_code() -> None:
    assert main([]) == 2
