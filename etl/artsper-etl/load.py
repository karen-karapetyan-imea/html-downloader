"""ETL: raw Artsper artist HTML pages -> Postgres.

Usage:
    .venv/bin/python etl/load.py --data /path/to/2026-08-21 [--workers N] [--limit N] [--resume]

--data is the crawl folder containing html/ and results.jsonl (absolute, or relative
to the current directory / the project root). --resume skips files already loaded.

Idempotent: re-running upserts artists/artworks/exhibitions and replaces each
artist's child rows, so it is safe to run repeatedly on the same or newer crawls.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).parent))
from parse import parse_page  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JSON_COLS = {"same_as", "breadcrumb", "gtm_page_context", "jsonld", "meta_tags", "gtm"}
CHILD_TABLES = ["artist_selections", "artist_attributes", "artist_movement_cards",
                "artist_catalog_filters", "artist_studio_images", "artist_similar",
                "artist_related_links", "artist_exhibitions"]


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def work(args):
    path, url = args
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return path.name, parse_page(fh.read(), url), None
    except Exception as e:  # keep going; error is recorded in crawl_pages
        return path.name, None, f"{type(e).__name__}: {e}"


def adapt(row):
    return {k: Jsonb(v) if k in JSON_COLS and v is not None else v for k, v in row.items()}


def insert(cur, table, rows, conflict=None):
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)})"
    if conflict:
        key, update = conflict
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
        sql += f" ON CONFLICT ({', '.join(key)}) DO " + (f"UPDATE SET {sets}" + (", updated_at = now()" if update else "") if sets else "NOTHING")
    cur.executemany(sql, [adapt(r) for r in rows])


def flush(conn, batch):
    pages, artists, children, artworks, exhibitions = [], [], {t: [] for t in CHILD_TABLES}, [], {}
    for meta, parsed, err in batch:
        page = dict(meta, page_type=None, artist_id=None, parse_status="error", parse_error=err)
        if parsed:
            page["page_type"] = parsed["page_type"]
            page["parse_status"] = "ok" if parsed["page_type"] == "artist" else "skipped"
        if parsed and parsed["page_type"] == "artist":
            a = parsed["artist"]
            aid = a["artist_id"]
            page["artist_id"] = aid
            artists.append(dict(a, source_file=meta["filename"], crawled_at=meta["crawled_at"]))
            for s_pos, s in enumerate(parsed["selections"], 1):
                children["artist_selections"].append({"artist_id": aid, "selection": s, "position": s_pos})
            for key, table in [("attributes", "artist_attributes"), ("movement_cards", "artist_movement_cards"),
                               ("filters", "artist_catalog_filters"), ("studio", "artist_studio_images"),
                               ("similar", "artist_similar"), ("related", "artist_related_links")]:
                children[table] += [dict(r, artist_id=aid) for r in parsed[key]]
            for e in parsed["exhibitions"]:
                pos = e.pop("position")
                exhibitions[e["exhibition_id"]] = e
                children["artist_exhibitions"].append({"artist_id": aid, "exhibition_id": e["exhibition_id"], "position": pos})
            artworks += [dict(w, artist_id=aid) for w in parsed["artworks"] if w["artwork_id"]]
        pages.append(page)

    # dedupe rows that share a primary key within the batch
    artists = list({a["artist_id"]: a for a in artists}.values())
    artworks = list({w["artwork_id"]: w for w in artworks}.values())
    ids = [a["artist_id"] for a in artists]
    with conn.transaction(), conn.cursor() as cur:
        insert(cur, "crawl_pages", pages, (["filename"], False))
        insert(cur, "artists", artists, (["artist_id"], True))
        for t in CHILD_TABLES:
            cur.execute(f"DELETE FROM {t} WHERE artist_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM artworks WHERE artist_id = ANY(%s)", (ids,))
        insert(cur, "exhibitions", list(exhibitions.values()), (["exhibition_id"], False))
        for t in CHILD_TABLES:
            insert(cur, t, children[t], None)
        insert(cur, "artworks", artworks, (["artwork_id"], True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="2026-08-21")
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() - 2, 1))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--resume", action="store_true", help="skip files already in crawl_pages")
    args = ap.parse_args()

    load_env()
    data = Path(args.data).expanduser()
    if not data.is_absolute() and not data.exists():
        data = ROOT / args.data
    html_dir = data / "html"
    files = {}
    for dirpath, _, names in os.walk(html_dir):
        for n in names:
            if n.endswith(".html"):
                files[n] = Path(dirpath) / n

    # crawl log: last entry per file wins
    log = {}
    with open(data / "results.jsonl") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("filename") in files:
                log[r["filename"]] = {k: r.get(k) for k in
                                      ("url", "status_code", "error", "block_detected",
                                       "block_reason", "duration_ms", "timestamp")}
    # autocommit: each flush() runs in its own explicit transaction
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    conn.execute((Path(__file__).parent / "schema.sql").read_text())

    names = sorted(files)
    if args.resume:
        loaded = {r[0] for r in conn.execute(
            "SELECT filename FROM crawl_pages WHERE parse_status <> 'error' AND crawl_folder = %s", (data.name,))}
        names = [n for n in names if n not in loaded]
        print(f"resume: {len(loaded)} already loaded", flush=True)
    names = names[: args.limit] if args.limit else names
    print(f"{len(names)} html files to load, {len(log)} matched in crawl log", flush=True)

    def meta(name):
        r = log.get(name, {})
        return {"filename": name, "crawl_folder": data.name, "url": r.get("url", ""), "status_code": r.get("status_code"),
                "error": r.get("error") or None, "block_detected": r.get("block_detected"),
                "block_reason": r.get("block_reason") or None, "duration_ms": r.get("duration_ms"),
                "crawled_at": r.get("timestamp")}

    t0, done, errors, batch = time.time(), 0, 0, []
    with ProcessPoolExecutor(args.workers) as ex:
        jobs = ((files[n], log.get(n, {}).get("url")) for n in names)
        for name, parsed, err in ex.map(work, jobs, chunksize=20):
            batch.append((meta(name), parsed, err))
            errors += err is not None
            if len(batch) >= args.batch:
                flush(conn, batch)
                done += len(batch)
                batch = []
                print(f"  {done}/{len(names)}  errors={errors}  {done / (time.time() - t0):.0f} pages/s", flush=True)
        if batch:
            flush(conn, batch)
            done += len(batch)
    conn.close()
    print(f"done: {done} pages in {time.time() - t0:.0f}s, parse errors={errors}")


if __name__ == "__main__":
    main()
