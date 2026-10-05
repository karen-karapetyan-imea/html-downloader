"""ETL: raw Saatchi Art HTML pages -> Postgres.

Usage:
    .venv/bin/python saatchi_etl/load.py --data /path/to/crawl-folder [--workers N] [--limit N] [--resume] [--no-raw]

--data is a folder of .html files (searched recursively; an html/ subfolder is used if present).
An optional results.jsonl crawl log next to them adds URL / status / timestamp per file.
Database: SAATCHI_DATABASE_URL (falls back to DATABASE_URL), read from the environment or .env.

Idempotent: artists and artworks are upserted (non-empty values win, so profile listings
and artwork detail pages merge into the same rows); child rows are replaced per artist or
artwork. Safe to re-run and to run on newer crawls.
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
MERGE_TABLES = {"artists", "artworks"}          # COALESCE-upsert: keep existing value when new one is empty
AUTO_COLS = {"updated_at", "loaded_at"}


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
        self.conn = conn
        self.store_raw = store_raw
        self.cols = defaultdict(list)
        for table, col in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() ORDER BY ordinal_position"):
            if col not in AUTO_COLS:
                self.cols[table].append(col)
        self.tag_ids, self.badge_ids = {}, {}

    # ---- generic upsert -------------------------------------------------------
    def upsert(self, cur, table, rows, key=None):
        if not rows:
            return
        cols = [c for c in self.cols[table] if any(c in r for r in rows)]
        values = [{c: (Jsonb(r.get(c)) if isinstance(r.get(c), dict) else r.get(c)) for c in cols} for r in rows]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)})"
        if key:
            upd = [c for c in cols if c not in key]
            if table in MERGE_TABLES:
                sets = [f"{c} = COALESCE(EXCLUDED.{c}, {table}.{c})" for c in upd] + ["updated_at = now()"]
            else:
                sets = [f"{c} = EXCLUDED.{c}" for c in upd]
            sql += f" ON CONFLICT ({', '.join(key)}) DO " + (f"UPDATE SET {', '.join(sets)}" if sets else "NOTHING")
        cur.executemany(sql, values)

    def lookup_ids(self, cur, table, cache, keys, key_cols, id_col, extra=None):
        """Insert missing lookup rows (tags/badges) and return {key: id}."""
        missing = [k for k in dict.fromkeys(keys) if k not in cache]
        if missing:
            rows = [dict(zip(key_cols, k if isinstance(k, tuple) else (k,)), **((extra or {}).get(k) or {}))
                    for k in missing]
            self.upsert(cur, table, rows, key_cols)
            where = " AND ".join(f"{c} = %s" for c in key_cols)
            for k in missing:
                params = k if isinstance(k, tuple) else (k,)
                cache[k] = cur.execute(f"SELECT {id_col} FROM {table} WHERE {where}", params).fetchone()[0]
        return cache

    # ---- one batch of parsed pages -----------------------------------------------
    def flush(self, batch):
        pages, raws, rates = [], [], {}
        artists, artworks = {}, {}
        badge_sets, collections, studio, profile_ids = {}, [], [], set()
        tags, images, products, options, region_prices = {}, {}, {}, {}, {}
        detail_ids = set()

        for meta, parsed, err in batch:
            page = dict(meta, parse_status="error", parse_error=err)
            if parsed:
                page.update(page_type=parsed["page_type"], canonical_url=parsed.get("canonical_url"),
                            artist_id=parsed.get("artist_id"), artwork_id=parsed.get("artwork_id"))
                page["url"] = page.get("url") or parsed.get("canonical_url")
                page["crawled_at"] = page.get("crawled_at") or parsed.get("crawled_at")
                page["parse_status"] = ("skipped" if parsed["page_type"] == "unknown"
                                        else "partial" if parsed.get("partial") else "ok")
            pages.append(page)
            if not parsed or parsed["page_type"] == "unknown":
                continue
            if self.store_raw and parsed.get("raw"):
                raws.append({"filename": meta["filename"], "page_type": parsed["page_type"], "state": parsed["raw"]})
            for r in parsed.get("rates", []):
                rates[(r["as_of_date"], r["currency_code"])] = r

            a = parsed.get("artist")
            if a and a.get("artist_id"):
                aid = a["artist_id"]
                a = dict(a, crawled_at=page["crawled_at"])
                if parsed["page_type"] == "artist_profile":
                    a["profile_file"] = meta["filename"]
                    if not parsed.get("partial"):
                        profile_ids.add(aid)
                        collections += [dict(c, artist_id=aid) for c in parsed["collections"]]
                        studio += [dict(s, artist_id=aid) for s in parsed["studio"]]
                merge_into(artists, aid, a)
                if "badges" in parsed:
                    badge_sets[aid] = parsed["badges"]

            for w in parsed["artworks"]:
                w = dict(w, crawled_at=page["crawled_at"])
                if w.get("has_detail"):
                    w["detail_file"] = meta["filename"]
                    detail_ids.add(w["artwork_id"])
                merge_into(artworks, w["artwork_id"], w)
            for t in parsed["tags"]:
                tags[(t["artwork_id"], t["kind"], t["name"])] = t
            for i in parsed["images"]:
                images[(i["artwork_id"], i["position"])] = i
            for p in parsed["products"]:
                products[p["sku"]] = p
            for o in parsed["options"]:
                options[(o["sku"], o["option_id"])] = o
            for rp in parsed["region_prices"]:
                region_prices[(rp["artwork_id"], rp["region_code"])] = rp

        with self.conn.transaction(), self.conn.cursor() as cur:
            self.upsert(cur, "crawl_pages", pages, ["filename"])
            self.upsert(cur, "raw_pages", raws, ["filename"])
            self.upsert(cur, "exchange_rates", list(rates.values()), ["as_of_date", "currency_code"])

            # artists + artist children
            self.upsert(cur, "artists", [artists[k] for k in sorted(artists)], ["artist_id"])
            badge_meta = {b["title"]: b for bs in badge_sets.values() for b in bs}
            self.lookup_ids(cur, "badges", self.badge_ids, list(badge_meta), ["title"], "badge_id",
                            {t: {"description": b["description"], "image_url": b["image_url"]}
                             for t, b in badge_meta.items()})
            cur.execute("DELETE FROM artist_badges WHERE artist_id = ANY(%s)", (sorted(badge_sets),))
            self.upsert(cur, "artist_badges", [
                {"artist_id": aid, "badge_id": self.badge_ids[b["title"]], "position": pos}
                for aid, bs in badge_sets.items()
                for pos, b in enumerate({b["title"]: b for b in bs}.values(), 1)])
            for table, rows in (("artist_collections", collections), ("artist_studio_images", studio)):
                cur.execute(f"DELETE FROM {table} WHERE artist_id = ANY(%s)", (sorted(profile_ids),))
                self.upsert(cur, table, rows)

            # artworks + artwork children
            self.upsert(cur, "artworks", [artworks[k] for k in sorted(artworks)], ["artwork_id"])
            self.lookup_ids(cur, "tags", self.tag_ids, [(k[1], k[2]) for k in tags], ["kind", "name"], "tag_id")
            kinds = sorted({(k[0], k[1]) for k in tags})
            cur.execute("DELETE FROM artwork_tags t USING tags g WHERE t.tag_id = g.tag_id AND "
                        "(t.artwork_id, g.kind) IN (SELECT * FROM unnest(%s::bigint[], %s::text[]))",
                        ([k[0] for k in kinds], [k[1] for k in kinds]))
            self.upsert(cur, "artwork_tags", list({
                (t["artwork_id"], self.tag_ids[(t["kind"], t["name"])]):
                    {"artwork_id": t["artwork_id"], "tag_id": self.tag_ids[(t["kind"], t["name"])], "position": t["position"]}
                for t in tags.values()}.values()))
            detail = sorted(detail_ids)
            for table in ("artwork_images", "products", "artwork_region_prices"):
                cur.execute(f"DELETE FROM {table} WHERE artwork_id = ANY(%s)", (detail,))
            cur.execute("DELETE FROM products WHERE sku = ANY(%s)", (sorted(products),))
            self.upsert(cur, "artwork_images", list(images.values()))
            self.upsert(cur, "products", list(products.values()))
            self.upsert(cur, "product_options", list(options.values()))
            self.upsert(cur, "artwork_region_prices", list(region_prices.values()))


def merge_into(store, key, row):
    """Combine rows for the same entity within a batch: later non-empty values win."""
    cur = store.setdefault(key, {})
    for k, v in row.items():
        if v is not None and v != [] or k not in cur:
            cur[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="saatchi-html")
    ap.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 2, 1))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=300)
    ap.add_argument("--resume", action="store_true", help="skip files already loaded from this folder")
    ap.add_argument("--no-raw", action="store_true", help="don't store raw page state (saves disk)")
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

    dsn = os.environ.get("SAATCHI_DATABASE_URL") or os.environ["DATABASE_URL"]
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
        mtime = datetime.fromtimestamp(files[name].stat().st_mtime, timezone.utc).isoformat() if not r else None
        return {"filename": name, "crawl_folder": data.name, "url": r.get("url"),
                "status_code": r.get("status_code"), "error": r.get("error") or None,
                "block_detected": r.get("block_detected"), "block_reason": r.get("block_reason") or None,
                "duration_ms": r.get("duration_ms"), "crawled_at": r.get("timestamp") or None,
                "_mtime": mtime}

    t0, done, errors, batch = time.time(), 0, 0, []

    def flush():
        nonlocal done, batch
        for m, parsed, _ in batch:   # file time is the last resort for crawled_at
            mt = m.pop("_mtime")
            if not m["crawled_at"] and not (parsed and parsed.get("crawled_at")):
                m["crawled_at"] = mt
        loader.flush(batch)
        done += len(batch)
        batch = []
        print(f"  {done}/{len(names)}  errors={errors}  {done / (time.time() - t0):.0f} pages/s", flush=True)

    with ProcessPoolExecutor(args.workers) as ex:
        for name, parsed, err in ex.map(work, (files[n] for n in names), chunksize=20):
            batch.append((meta(name), parsed, err))
            errors += err is not None
            if len(batch) >= args.batch:
                flush()
        if batch:
            flush()
    conn.close()
    print(f"done: {done} pages in {time.time() - t0:.0f}s, parse errors={errors}")


if __name__ == "__main__":
    main()
