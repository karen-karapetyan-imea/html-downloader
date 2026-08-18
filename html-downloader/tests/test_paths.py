from __future__ import annotations

from datetime import date
from pathlib import Path

from html_downloader.paths import (
    diff_file,
    html_dir,
    job_dir,
    known_result_paths,
    lastmod_state_file,
    manifest_file,
    parse_crawl_date,
    results_file,
    sitemap_all_file,
    urls_file,
)


def test_parse_crawl_date_explicit() -> None:
    assert parse_crawl_date("2026-08-19") == date(2026, 8, 19)


def test_job_paths(tmp_path: Path) -> None:
    job = job_dir(tmp_path, "saatchi", date(2026, 8, 19))
    assert job == tmp_path / "saatchi" / "2026-08-19"
    assert html_dir(job) == job / "html"
    assert urls_file(job) == job / "urls.txt"
    assert sitemap_all_file(job) == job / "sitemap_all.txt"
    assert results_file(job) == job / "results.jsonl"
    assert diff_file(job) == job / "diff.json"
    assert manifest_file(job) == job / "manifest.json"


def test_lastmod_state_file(tmp_path: Path) -> None:
    assert lastmod_state_file(tmp_path, "artsy") == tmp_path / "artsy_lastmod.json"


def test_known_result_paths(tmp_path: Path) -> None:
    market = tmp_path / "saatchi"
    old = market / "2026-08-01"
    old.mkdir(parents=True)
    (old / "results.jsonl").write_text("{}\n", encoding="utf-8")
    (market / "2026-08-02").mkdir()
    found = known_result_paths(tmp_path, "saatchi")
    assert found == [old / "results.jsonl"]
