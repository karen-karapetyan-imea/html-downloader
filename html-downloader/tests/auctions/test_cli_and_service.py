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
            "--incremental",
        ]
    )
    assert la_args.auction_house == "liveauctioneers"
    assert la_args.max_sitemaps == 25
    assert la_args.min_urls == 50000

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
    assert len(urls) == 2
    meta = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
    assert meta["auction_house"] == "liveauctioneers"
    assert (state_root / "auctions" / "liveauctioneers.json").is_file()


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


def test_main_help_exit_code() -> None:
    assert main([]) == 2
