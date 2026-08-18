from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from html_downloader.discover.service import run_discover
from html_downloader.discover.sitemap import SitemapEntry
from html_downloader.paths import diff_file, sitemap_all_file, urls_file


def _entry(url: str, entity_type: str, entity_id: str, lastmod: str) -> SitemapEntry:
    return SitemapEntry(url=url, lastmod=lastmod, entity_type=entity_type, entity_id=entity_id)


def test_discover_writes_dated_job_files(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view",
            "artwork",
            "9336593",
            "2026-06-01T00:00:00+00:00",
        ),
        _entry(
            "https://www.saatchiart.com/account/profile/735695",
            "artist",
            "735695",
            "2026-06-02T00:00:00+00:00",
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"

    with patch("html_downloader.discover.service.fetch_entries", return_value=entries):
        result = run_discover(
            marketplace="saatchi",
            data_root=data_root,
            state_root=state_root,
            crawl_date=date(2026, 8, 19),
            incremental=False,
            include_updates=True,
            update_state=False,
            proxy_file=None,
            concurrency=1,
            dry_run=False,
        )

    job = result.job
    assert job == data_root / "saatchi" / "2026-08-19"
    urls = urls_file(job).read_text(encoding="utf-8").strip().splitlines()
    all_urls = sitemap_all_file(job).read_text(encoding="utf-8").strip().splitlines()
    assert len(urls) == 2
    assert urls == all_urls
    report = json.loads(diff_file(job).read_text(encoding="utf-8"))
    assert report["stats"]["entity_urls"] == 2
    assert result.crawl_count == 2


def test_discover_incremental_uses_prior_results(tmp_path: Path) -> None:
    entries = [
        _entry(
            "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view",
            "artwork",
            "9336593",
            "2026-06-01T00:00:00+00:00",
        ),
        _entry(
            "https://www.saatchiart.com/account/profile/999",
            "artist",
            "999",
            "2026-06-02T00:00:00+00:00",
        ),
    ]
    data_root = tmp_path / "data"
    prior = data_root / "saatchi" / "2026-08-01"
    prior.mkdir(parents=True)
    (prior / "results.jsonl").write_text(
        '{"url": "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view"}\n',
        encoding="utf-8",
    )

    with patch("html_downloader.discover.service.fetch_entries", return_value=entries):
        result = run_discover(
            marketplace="saatchi",
            data_root=data_root,
            state_root=tmp_path / "state",
            crawl_date=date(2026, 8, 19),
            incremental=True,
            include_updates=True,
            update_state=False,
            proxy_file=None,
            concurrency=1,
            dry_run=False,
        )

    crawl_urls = urls_file(result.job).read_text(encoding="utf-8").strip().splitlines()
    assert crawl_urls == ["https://www.saatchiart.com/account/profile/999"]
    all_urls = sitemap_all_file(result.job).read_text(encoding="utf-8").strip().splitlines()
    assert len(all_urls) == 2


def test_artsy_discover_requires_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWLER_PROXY", raising=False)
    empty = tmp_path / "proxy.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="proxy-file"):
        run_discover(
            marketplace="artsy",
            data_root=tmp_path / "data",
            state_root=tmp_path / "state",
            crawl_date=date(2026, 8, 19),
            incremental=False,
            include_updates=True,
            update_state=False,
            proxy_file=str(empty),
            concurrency=1,
            dry_run=True,
        )
