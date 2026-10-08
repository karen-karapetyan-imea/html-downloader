import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from artists.__main__ import main
from artists.schema import ARTIST_SCHEMA
from artists.spec import ARTISTS
from artworks.__main__ import main as artwork_main
from artworks.schema import ARTWORK_SCHEMA
from artworks.spec import ARTWORKS
from etl_core.compact import compact as compact_dataset
from etl_core.crawl import detect_crawl_date, load_crawl_log
from etl_core.layout import CHUNKS_DIR, ERRORS_NAME, MANIFEST_NAME, current_dir, snapshot_dir
from etl_core.pipeline import RunConfig, run_snapshot
from tests import builders


def make_crawl(root: Path, crawl_date: str, pages: dict[str, str], log: list[dict] | None = None) -> Path:
    data = root / crawl_date
    for name, html in pages.items():
        path = data / "html" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    if log:
        (data / "results.jsonl").write_text("\n".join(json.dumps(r) for r in log) + "\nnot json\n")
    return data


def saatchi_pages(city: str = "Berlin") -> dict[str, str]:
    return {
        "a.html": builders.saatchi_artist(1, city=city),
        "b.html": builders.saatchi_artist(2, city=city),
        "x/c.html": builders.saatchi_artwork(),
        "x/d.html": builders.saatchi_broken(),
        "x/e.html": builders.saatchi_artist(1, city=city),
    }


def count_rows(path: Path) -> int:
    return duckdb.sql(f"SELECT count(*) FROM read_parquet('{path}/part-*.parquet')").fetchone()[0]


def config(data: Path, out: Path, **kw) -> RunConfig:
    return RunConfig(
        dataset=ARTISTS, platform="saatchi", data_dir=data, out=out, crawl_date=detect_crawl_date(data), **kw
    )


def compact(platform: str, out: Path):
    return compact_dataset(ARTISTS, platform, out)


@pytest.mark.parametrize("workers", [1, 2])
def test_run_writes_snapshot(tmp_path, workers):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    manifest = run_snapshot(config(data, tmp_path / "out", workers=workers, chunk_size=2))

    assert manifest | {"files_total": 5, "rows": 3, "skipped": 1, "no_id": 0, "errors": 1} == manifest
    snap = snapshot_dir(tmp_path / "out", "saatchi", date(2026, 9, 23))
    assert json.loads((snap / MANIFEST_NAME).read_text())["chunks"] == 3
    errors = pq.read_table(snap / ERRORS_NAME).to_pylist()
    assert [e["filename"] for e in errors] == ["d.html"]
    for part in snap.glob("part-*.parquet"):
        assert pq.read_schema(part).equals(ARTIST_SCHEMA)
    assert count_rows(snap) == 3


def test_resume_redoes_only_missing_chunks(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    out = tmp_path / "out"
    first = run_snapshot(config(data, out, chunk_size=2))
    snap = snapshot_dir(out, "saatchi", date(2026, 9, 23))
    (snap / CHUNKS_DIR / "part-00002.json").unlink()
    (snap / "part-00002.parquet").unlink()
    (snap / "part-00000.parquet.tmp").write_text("leftover")

    second = run_snapshot(config(data, out, chunk_size=2, resume=True))

    assert second["resumed_chunks"] == 2
    assert second["rows"] == first["rows"]
    assert (snap / "part-00002.parquet").exists()
    assert not (snap / "part-00000.parquet.tmp").exists()


def test_resume_with_different_chunk_size_is_rejected(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    run_snapshot(config(data, tmp_path / "out", chunk_size=2))
    with pytest.raises(ValueError, match="cannot resume"):
        run_snapshot(config(data, tmp_path / "out", chunk_size=3, resume=True))


def test_fresh_run_replaces_snapshot(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    out = tmp_path / "out"
    run_snapshot(config(data, out, chunk_size=1))
    run_snapshot(config(data, out, chunk_size=5))
    snap = snapshot_dir(out, "saatchi", date(2026, 9, 23))
    assert sorted(p.name for p in snap.glob("part-*.parquet")) == ["part-00000.parquet"]


def test_compact_dedups_snapshots_and_keeps_latest_crawl(tmp_path):
    out = tmp_path / "out"
    old = make_crawl(
        tmp_path,
        "2026-08-01",
        {
            "a.html": builders.saatchi_artist(1, city="Old"),
            "z.html": builders.saatchi_artist(9, city="Gone"),
        },
    )
    new = make_crawl(tmp_path, "2026-09-23", saatchi_pages(city="New"))
    run_snapshot(config(old, out, chunk_size=1))
    run_snapshot(config(new, out, chunk_size=1))
    # Same server timestamp in both builders: the crawl date has to break the tie.
    result = compact("saatchi", out)

    assert (result.snapshots, result.snapshots_compacted, result.current_rows) == (2, 2, 3)
    new_snap = snapshot_dir(out, "saatchi", date(2026, 9, 23))
    assert sorted(p.name for p in new_snap.glob("part-*.parquet")) == ["part-00000.parquet"]
    assert json.loads((new_snap / MANIFEST_NAME).read_text())["rows_after_dedup"] == 2
    assert (new_snap / ERRORS_NAME).exists()

    current = current_dir(out, "saatchi")
    rows = duckdb.sql(
        f"SELECT platform_artist_id, city FROM read_parquet('{current}/part-*.parquet') ORDER BY 1"
    ).fetchall()
    assert rows == [("1", "New"), ("2", "New"), ("9", "Gone")]
    schema = pq.read_schema(current / "part-00000.parquet")
    assert [(f.name, f.type) for f in schema] == [(f.name, f.type) for f in ARTIST_SCHEMA]

    assert compact("saatchi", out).snapshots_compacted == 0


def test_compact_skips_unfinished_snapshot(tmp_path):
    out = tmp_path / "out"
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    run_snapshot(config(data, out, chunk_size=2))
    unfinished = snapshot_dir(out, "saatchi", date(2026, 9, 30))
    unfinished.mkdir(parents=True)
    assert compact("saatchi", out).snapshots == 1


def test_compact_without_artist_rows_writes_empty_current(tmp_path):
    out = tmp_path / "out"
    data = make_crawl(tmp_path, "2026-09-23", {"c.html": builders.saatchi_artwork()})
    run_snapshot(config(data, out))
    assert compact("saatchi", out).current_rows == 0
    part = current_dir(out, "saatchi") / "part-00000.parquet"
    assert pq.read_metadata(part).num_rows == 0
    assert count_rows(current_dir(out, "saatchi")) == 0


def test_compact_without_snapshots_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        compact("saatchi", tmp_path / "out")


OLDER_THAN_ARTIST_PAGES_MS = 1700000000000  # 2023; builders.saatchi_artist is stamped 2025


def artwork_config(data: Path, out: Path, **kw) -> RunConfig:
    return RunConfig(
        dataset=ARTWORKS, platform="saatchi", data_dir=data, out=out, crawl_date=detect_crawl_date(data), **kw
    )


def current_artworks(out: Path) -> list[tuple]:
    return duckdb.sql(
        f"SELECT platform_artwork_id, price, has_detail, mediums "
        f"FROM read_parquet('{current_dir(out, 'saatchi')}/part-*.parquet') ORDER BY 1"
    ).fetchall()


@pytest.mark.parametrize("workers", [1, 2])
def test_artworks_detail_page_beats_newer_card_within_a_crawl(tmp_path, workers):
    out = tmp_path / "out"
    data = make_crawl(
        tmp_path,
        "2026-09-23",
        {
            "a.html": builders.saatchi_artist(artworks=[builders.saatchi_card(5), builders.saatchi_card(6)]),
            "b.html": builders.saatchi_artwork(server_ms=OLDER_THAN_ARTIST_PAGES_MS),
            "c.html": builders.saatchi_artist(2),
        },
    )
    manifest = run_snapshot(artwork_config(data, out, workers=workers, chunk_size=1))
    assert manifest | {"files_total": 3, "rows": 3, "skipped": 0, "no_id": 1, "errors": 0} == manifest

    assert compact_dataset(ARTWORKS, "saatchi", out).current_rows == 2
    assert current_artworks(out) == [
        ("5", Decimal("1500.00"), True, ["Oil"]),
        ("6", Decimal("1000.00"), False, ["Oil", "Acrylic"]),
    ]
    schema = pq.read_schema(current_dir(out, "saatchi") / "part-00000.parquet")
    assert [(f.name, f.type) for f in schema] == [(f.name, f.type) for f in ARTWORK_SCHEMA]


def test_artworks_newest_crawl_wins_across_crawls(tmp_path):
    out = tmp_path / "out"
    old = make_crawl(tmp_path, "2026-08-01", {"b.html": builders.saatchi_artwork()})
    new = make_crawl(
        tmp_path, "2026-09-23", {"a.html": builders.saatchi_artist(artworks=[builders.saatchi_card(5)])}
    )
    run_snapshot(artwork_config(old, out))
    run_snapshot(artwork_config(new, out))

    compact_dataset(ARTWORKS, "saatchi", out)
    assert current_artworks(out) == [("5", Decimal("1000.00"), False, ["Oil", "Acrylic"])]


def test_artwork_cli_run_and_compact(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", {"b.html": builders.saatchi_artwork()})
    out = tmp_path / "out"
    run = ["run", "--platform", "saatchi", "--data", str(data), "--out", str(out), "--workers", "1"]
    assert artwork_main(run) == 0
    assert artwork_main(["compact", "--platform", "saatchi", "--out", str(out)]) == 0
    assert pq.read_metadata(current_dir(out, "saatchi") / "part-00000.parquet").num_rows == 1


def test_resume_of_another_dataset_is_rejected(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    run_snapshot(config(data, tmp_path / "out", chunk_size=2))
    with pytest.raises(ValueError, match="cannot resume"):
        run_snapshot(artwork_config(data, tmp_path / "out", chunk_size=2, resume=True))


def test_crawl_log_and_date_detection(tmp_path):
    data = make_crawl(
        tmp_path,
        "2026-09-23",
        {"a.html": "<html></html>"},
        log=[
            {"filename": "a.html", "url": "https://x/1", "timestamp": "2026-09-23T10:00:00"},
            {"filename": "a.html", "url": "https://x/2", "timestamp": "2026-09-23T11:00:00+02:00"},
        ],
    )
    entry = load_crawl_log(data)["a.html"]
    assert entry.url == "https://x/2"
    assert entry.crawled_at.isoformat() == "2026-09-23T09:00:00+00:00"
    assert detect_crawl_date(data / "html") == date(2026, 9, 23)
    assert detect_crawl_date(tmp_path, "2026-01-02") == date(2026, 1, 2)
    with pytest.raises(ValueError):
        detect_crawl_date(tmp_path / "undated")


def test_cli_run_and_compact(tmp_path):
    data = make_crawl(tmp_path, "2026-09-23", saatchi_pages())
    out = tmp_path / "out"
    assert (
        main(["run", "--platform", "saatchi", "--data", str(data), "--out", str(out), "--workers", "1"]) == 0
    )
    assert main(["compact", "--platform", "saatchi", "--out", str(out)]) == 0
    assert (current_dir(out, "saatchi") / "part-00000.parquet").exists()
    assert (
        main(
            [
                "run",
                "--platform",
                "saatchi",
                "--data",
                str(tmp_path / "missing"),
                "--out",
                str(out),
                "--crawl-date",
                "2026-01-01",
            ]
        )
        == 2
    )
