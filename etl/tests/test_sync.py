import json
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from artists.spec import ARTISTS
from artworks.__main__ import main as artwork_main
from etl_core import sync as sync_module
from etl_core.layout import CHUNKS_DIR, MANIFEST_NAME, current_dir, snapshot_dir
from etl_core.pipeline import RunConfig, run_snapshot
from etl_core.sync import SyncConfig, find_pending, sync
from tests.builders import make_crawl, saatchi_pages, write_crawl_manifest


def crawl(
    data_root: Path,
    crawl_date: str = "2026-09-23",
    status: str | None = "completed",
    platform: str = "saatchi",
    **manifest,
) -> Path:
    data = make_crawl(data_root / platform, crawl_date, saatchi_pages())
    if status is not None:
        write_crawl_manifest(data, status=status, **manifest)
    return data


def pending(data_root: Path, out: Path, **kw) -> list[tuple[str, str, bool]]:
    return [
        (p.platform, p.crawl_date.isoformat(), p.resume) for p in find_pending(ARTISTS, data_root, out, **kw)
    ]


def config(data_root: Path, out: Path, **kw) -> SyncConfig:
    return SyncConfig(data_root=data_root, out=out, workers=1, chunk_size=2, **kw)


def snapshot_manifest(out: Path, crawl_date: str = "2026-09-23") -> dict:
    snap = snapshot_dir(out, "saatchi", date.fromisoformat(crawl_date))
    return json.loads((snap / MANIFEST_NAME).read_text())


def test_finished_crawls_are_pending_and_running_ones_are_not(tmp_path):
    crawl(tmp_path / "data", "2026-08-01", status="completed")
    crawl(tmp_path / "data", "2026-08-08", status="failed")
    crawl(tmp_path / "data", "2026-08-15", status="running", finished_at=None)

    assert pending(tmp_path / "data", tmp_path / "out") == [
        ("saatchi", "2026-08-01", False),
        ("saatchi", "2026-08-08", False),
    ]


def test_crawls_without_manifest_need_opt_in(tmp_path):
    crawl(tmp_path / "data", status=None)
    assert pending(tmp_path / "data", tmp_path / "out") == []
    assert pending(tmp_path / "data", tmp_path / "out", include_unmanaged=True) == [
        ("saatchi", "2026-09-23", False)
    ]


def test_unknown_platforms_undated_folders_and_bad_manifests_are_ignored(tmp_path):
    data_root = tmp_path / "data"
    crawl(data_root, platform="singulart")
    (data_root / "saatchi" / "latest").mkdir(parents=True)
    (data_root / "saatchi" / "2026-13-45").mkdir()
    crawl(data_root, "2026-09-23")
    (data_root / "saatchi" / "2026-09-23" / "manifest.json").write_text("{half writ")

    assert pending(data_root, tmp_path / "out") == []


def test_platform_filter(tmp_path):
    crawl(tmp_path / "data")
    assert pending(tmp_path / "data", tmp_path / "out", platforms=("artsy",)) == []
    with pytest.raises(ValueError, match="unknown platform"):
        pending(tmp_path / "data", tmp_path / "out", platforms=("nope",))


def test_sync_builds_snapshots_and_current_then_does_nothing(tmp_path):
    data_root, out = tmp_path / "data", tmp_path / "out"
    crawl(data_root, "2026-08-01")
    crawl(data_root, "2026-09-23")

    result = sync(ARTISTS, config(data_root, out))

    assert result.ok
    assert [p.crawl_date.isoformat() for p in result.processed] == ["2026-08-01", "2026-09-23"]
    assert result.compacted == ["saatchi"]
    assert pq.read_metadata(current_dir(out, "saatchi") / "part-00000.parquet").num_rows == 2

    again = sync(ARTISTS, config(data_root, out))
    assert (again.pending, again.compacted) == ([], [])


def test_crawl_finished_after_the_etl_run_is_redone(tmp_path):
    data_root, out = tmp_path / "data", tmp_path / "out"
    data = crawl(data_root)
    sync(ARTISTS, config(data_root, out))

    write_crawl_manifest(data, finished_at="2099-01-01T00:00:00Z")
    assert pending(data_root, out) == [("saatchi", "2026-09-23", False)]
    assert sync(ARTISTS, config(data_root, out)).compacted == ["saatchi"]
    assert pending(data_root, out) == [("saatchi", "2026-09-23", False)]  # still "newer" than the run


def test_interrupted_snapshot_is_resumed(tmp_path):
    data_root, out = tmp_path / "data", tmp_path / "out"
    data = crawl(data_root)
    run_snapshot(RunConfig(ARTISTS, "saatchi", data, out, date(2026, 9, 23), chunk_size=2))
    snap = snapshot_dir(out, "saatchi", date(2026, 9, 23))
    (snap / MANIFEST_NAME).unlink()
    (snap / CHUNKS_DIR / "part-00002.json").unlink()

    assert pending(data_root, out) == [("saatchi", "2026-09-23", True)]
    assert sync(ARTISTS, config(data_root, out)).ok
    assert snapshot_manifest(out)["resumed_chunks"] == 2


def test_resume_with_other_settings_starts_over(tmp_path):
    data_root, out = tmp_path / "data", tmp_path / "out"
    data = crawl(data_root)
    run_snapshot(RunConfig(ARTISTS, "saatchi", data, out, date(2026, 9, 23), chunk_size=3))
    (snapshot_dir(out, "saatchi", date(2026, 9, 23)) / MANIFEST_NAME).unlink()

    assert sync(ARTISTS, config(data_root, out)).ok
    manifest = snapshot_manifest(out)
    assert (manifest["resumed_chunks"], manifest["chunk_size"]) == (0, 2)


def test_failed_crawl_does_not_block_the_others(tmp_path, monkeypatch):
    data_root, out = tmp_path / "data", tmp_path / "out"
    crawl(data_root, "2026-08-01")
    crawl(data_root, "2026-09-23")
    real_run = sync_module.run_snapshot

    def flaky_run(run_config: RunConfig):
        if run_config.crawl_date == date(2026, 8, 1):
            raise OSError("disk on fire")
        return real_run(run_config)

    monkeypatch.setattr(sync_module, "run_snapshot", flaky_run)
    result = sync(ARTISTS, config(data_root, out))

    assert not result.ok
    assert [p.crawl_date for p in result.failed] == [date(2026, 8, 1)]
    assert [p.crawl_date for p in result.processed] == [date(2026, 9, 23)]
    assert result.compacted == ["saatchi"]
    assert pending(data_root, out) == [("saatchi", "2026-08-01", False)]


def test_dry_run_writes_nothing(tmp_path):
    crawl(tmp_path / "data")
    result = sync(ARTISTS, config(tmp_path / "data", tmp_path / "out", dry_run=True))
    assert len(result.pending) == 1
    assert (result.processed, result.compacted) == ([], [])
    assert not (tmp_path / "out").exists()


def test_cli_sync_exit_codes(tmp_path, monkeypatch):
    data_root, out = tmp_path / "data", tmp_path / "out"
    crawl(data_root)
    args = ["sync", "--data-root", str(data_root), "--out", str(out), "--workers", "1"]

    assert artwork_main(args) == 0
    assert pq.read_metadata(current_dir(out, "saatchi") / "part-00000.parquet").num_rows == 1

    def broken_run(run_config: RunConfig):
        raise OSError("boom")

    crawl(data_root, "2026-09-30")
    monkeypatch.setattr(sync_module, "run_snapshot", broken_run)
    assert artwork_main(args) == 1
