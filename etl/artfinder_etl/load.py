"""ETL: raw Artfinder HTML pages -> Postgres.

Usage:
    .venv/bin/python artfinder_etl/load.py --data /path/to/crawl-folder [--workers N] [--limit N] [--resume] [--no-raw]

--data is a folder of .html files (searched recursively; an html/ subfolder is used if present).
An optional results.jsonl crawl log next to them adds URL / status / timestamp per file.
Database: ARTFINDER_DATABASE_URL (falls back to DATABASE_URL), from the environment or .env.

Idempotent: entities are upserted (non-empty values win, so data about the same artist or
artwork from different pages is merged); child rows are replaced per artist / artwork.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).parent))
from parse import parse_page  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Write order respects foreign keys. Entity tables merge; the rest are replaced per owner.
TABLE_ORDER = ["currency_rates", "artists", "artworks",
               "artist_social_links", "artist_awards", "artist_education", "artist_events",
               "artist_featured_in", "artist_collections",
               "artwork_images", "artwork_categories", "artwork_featured_collections", "artwork_print_editions"]
MERGE_TABLES = {"artists", "artworks"}
JSON_COLS = {"state"}
AUTO_COLS = {"updated_at", "loaded_at"}
PRIORITY_COL = {"artists": "has_page", "artworks": "has_detail"}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def work(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return path.name, parse_page(fh.read()), None
    except Exception as e:  # keep going; the error is recorded in crawl_pages
        return path.name, None, f"{type(e).__name__}: {e}"


class Loader:
    def __init__(self, conn, store_raw):
        self.conn, self.store_raw = conn, store_raw
        self.cols, self.pk = defaultdict(list), defaultdict(list)
        for table, col in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() ORDER BY ordinal_position"):
            if col not in AUTO_COLS:
                self.cols[table].append(col)
        for table, col in conn.execute(
                "SELECT tc.table_name, k.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage k USING (constraint_schema, constraint_name) "
                "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = current_schema() "
                "ORDER BY k.ordinal_position"):
            self.pk[table].append(col)

    def upsert(self, cur, table, rows):
        if not rows:
            return
        cols = [c for c in self.cols[table] if any(c in r for r in rows)]
        key = self.pk[table]
        values = [{c: (Jsonb(no_nul(r.get(c))) if c in JSON_COLS and r.get(c) is not None else no_nul(r.get(c)))
                   for c in cols} for r in rows]
        upd = [c for c in cols if c not in key]
        if table in MERGE_TABLES:
            # Values from the entity's own page win over summaries seen on other pages.
            prio = PRIORITY_COL.get(table)
            if prio in cols:
                own = f"({table}.{prio} IS TRUE AND EXCLUDED.{prio} IS NOT TRUE)"
                sets = [f"{c} = CASE WHEN {own} THEN COALESCE({table}.{c}, EXCLUDED.{c}) "
                        f"ELSE COALESCE(EXCLUDED.{c}, {table}.{c}) END" for c in upd]
            else:
                sets = [f"{c} = COALESCE(EXCLUDED.{c}, {table}.{c})" for c in upd]
            if "updated_at" in self.cols_all(table):
                sets.append("updated_at = now()")
        else:
            sets = [f"{c} = EXCLUDED.{c}" for c in upd]
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)}) "
               f"ON CONFLICT ({', '.join(key)}) DO " + (f"UPDATE SET {', '.join(sets)}" if sets else "NOTHING"))
        cur.executemany(sql, values)

    def cols_all(self, table):
        return self.cols[table] + (["updated_at"] if table in ("artists", "artworks") else [])

    def flush(self, batch):
        pages, raws = [], []
        rows = defaultdict(dict)                     # table -> {pk tuple: merged row}
        replace = defaultdict(lambda: defaultdict(set))  # table -> column -> values
        for meta, parsed, err in batch:
            page = dict(meta, parse_status="error", parse_error=err)
            if parsed:
                page.update(page_type=parsed["page_type"], canonical_url=parsed.get("canonical_url"),
                            artist_id=parsed.get("artist_id"), artwork_id=parsed.get("artwork_id"),
                            parse_status="skipped" if parsed["page_type"] == "unknown" else "ok")
                page["url"] = page.get("url") or parsed.get("canonical_url")
            pages.append(page)
            if not parsed or parsed["page_type"] == "unknown":
                continue
            if self.store_raw and parsed.get("raw"):
                raws.append({"filename": meta["filename"], "page_type": parsed["page_type"], "state": parsed["raw"]})
            for table, (col, vals) in parsed["replace"].items():
                replace[table][col].update(v for v in vals if v is not None)
            owner = {"artists": "artist_id", "artworks": "artwork_id"}
            for table, trows in parsed["rows"].items():
                for r in trows:
                    if table == "currency_rates":        # rates are dated by crawl day
                        r = dict(r, as_of_date=str(meta["crawled_at"])[:10])
                    if table in owner:
                        r = dict(r, crawled_at=meta["crawled_at"])
                        if (table == "artists" and r.get("has_page")) or (table == "artworks" and r.get("has_detail")):
                            r["source_file"] = meta["filename"]
                    merge_into(rows[table], tuple(r.get(k) for k in self.pk[table]), r)

        with self.conn.transaction(), self.conn.cursor() as cur:
            self.upsert(cur, "crawl_pages", pages)
            self.upsert(cur, "raw_pages", raws)
            for table in TABLE_ORDER:
                for col, vals in replace.get(table, {}).items():
                    cur.execute(f"DELETE FROM {table} WHERE {col} = ANY(%s)", (sorted(vals),))
                self.upsert(cur, table, [rows[table][k] for k in sorted(rows[table], key=str)])


def no_nul(v):
    """Postgres text/jsonb can't hold NUL characters; some pages contain them."""
    if isinstance(v, str):
        return v.replace("\x00", "")
    if isinstance(v, list):
        return [no_nul(x) for x in v]
    if isinstance(v, dict):
        return {no_nul(k): no_nul(x) for k, x in v.items()}
    return v


def merge_into(store, key, row):
    """Combine rows for the same entity within a batch: later non-empty values win,
    except that a summary row never overrides a row from the entity's own page."""
    cur = store.setdefault(key, {})
    own = lambda r: r.get("has_page") or r.get("has_detail")
    keep_existing = own(cur) and not own(row)
    for k, v in row.items():
        if k not in cur or (v is not None and (not keep_existing or cur[k] is None)):
            cur[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="artfinder-html")
    ap.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 2, 1))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=300)
    ap.add_argument("--resume", action="store_true", help="skip files already loaded from this folder")
    ap.add_argument("--no-raw", action="store_true", help="don't store raw structured data (saves disk)")
    args = ap.parse_args()

    load_env()
    data = Path(args.data).expanduser()
    if not data.is_absolute() and not data.exists():
        data = ROOT / args.data
    html_dir = data / "html" if (data / "html").is_dir() else data
    files = {}
    for dirpath, _, names in os.walk(html_dir):
        for n in names:
            if n.endswith(".html"):
                files[n] = Path(dirpath) / n

    log = {}
    if (data / "results.jsonl").exists():
        with open(data / "results.jsonl") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("filename") in files:
                    log[r["filename"]] = r

    dsn = os.environ.get("ARTFINDER_DATABASE_URL") or os.environ["DATABASE_URL"]
    conn = psycopg.connect(dsn, autocommit=True)   # each flush() runs in its own transaction
    conn.execute((Path(__file__).parent / "schema.sql").read_text())
    loader = Loader(conn, store_raw=not args.no_raw)

    names = sorted(files)
    if args.resume:
        loaded = {r[0] for r in conn.execute(
            "SELECT filename FROM crawl_pages WHERE parse_status <> 'error' AND crawl_folder = %s", (data.name,))}
        names = [n for n in names if n not in loaded]
        print(f"resume: {len(loaded)} already loaded", flush=True)
    names = names[: args.limit] if args.limit else names
    print(f"{len(names)} html files to load from {html_dir}, {len(log)} matched in crawl log", flush=True)

    def meta(name):
        r = log.get(name, {})
        crawled = r.get("timestamp") or datetime.fromtimestamp(files[name].stat().st_mtime, timezone.utc).isoformat()
        return {"filename": name, "crawl_folder": data.name, "url": r.get("url"),
                "status_code": r.get("status_code"), "error": r.get("error") or None,
                "block_detected": r.get("block_detected"), "block_reason": r.get("block_reason") or None,
                "duration_ms": r.get("duration_ms"), "crawled_at": crawled}

    t0, done, errors, batch = time.time(), 0, 0, []
    with ProcessPoolExecutor(args.workers) as ex:
        for name, parsed, err in ex.map(work, (files[n] for n in names), chunksize=20):
            batch.append((meta(name), parsed, err))
            errors += err is not None
            if len(batch) >= args.batch:
                loader.flush(batch)
                done += len(batch)
                batch = []
                print(f"  {done}/{len(names)}  errors={errors}  {done / (time.time() - t0):.0f} pages/s", flush=True)
        if batch:
            loader.flush(batch)
            done += len(batch)
    conn.close()
    print(f"done: {done} pages in {time.time() - t0:.0f}s, parse errors={errors}")


if __name__ == "__main__":
    main()
