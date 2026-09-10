#!/usr/bin/env python3
"""Readonly probe of Invaluable sitemap / URL patterns."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

try:
    from curl_cffi import requests as crequests

    BACKEND = "curl_cffi"

    def get(url: str, sleep: float = 2.0):
        time.sleep(sleep)
        r = crequests.get(
            url,
            impersonate="chrome131",
            timeout=60,
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.invaluable.com/",
            },
        )
        print(f"status={r.status_code} size={len(r.content)} backend={BACKEND} url={url}")
        return r

except Exception as exc:  # noqa: BLE001
    import httpx

    BACKEND = f"httpx({exc})"

    def get(url: str, sleep: float = 2.0):
        time.sleep(sleep)
        r = httpx.get(
            url,
            timeout=60,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.invaluable.com/",
            },
        )
        print(f"status={r.status_code} size={len(r.content)} backend={BACKEND} url={url}")
        return r


def is_waf(content: bytes) -> bool:
    head = content[:800].lower()
    return b"awswaf" in head or b"challenge.js" in head or b"gokuprops" in head


def analyze_html_links(html: str, label: str) -> list[tuple[str, str]]:
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S)
    cleaned: list[tuple[str, str]] = []
    for href, text in hrefs:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        cleaned.append((href, text))
    prefs: Counter[str] = Counter()
    examples: dict[str, tuple[str, str]] = {}
    for href, text in cleaned:
        if href.startswith("#"):
            continue
        full = urljoin("https://www.invaluable.com/", href)
        path = urlparse(full).path
        parts = [p for p in path.split("/") if p]
        key = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "/")
        prefs[key] += 1
        examples.setdefault(key, (full, text[:120]))
    print(f"=== {label} link prefixes ===")
    for key, count in prefs.most_common(60):
        print(f"{count:4d} {key} :: {examples[key][0]} :: {examples[key][1]!r}")
    return cleaned


def main() -> None:
    print("BACKEND_INIT", BACKEND)

    # XML index
    r = get("https://www.invaluable.com/sitemap_inv_com-index.xml", sleep=1)
    (OUT / "sitemap_index.xml").write_bytes(r.content)
    if r.status_code == 200 and not is_waf(r.content):
        print(r.text)
        locs = re.findall(r"<loc>(.*?)</loc>", r.text)
        print("N_LOCS", len(locs))
        for i, loc in enumerate(locs[:10]):
            rr = get(loc, sleep=3)
            (OUT / f"child_{i}.xml").write_bytes(rr.content)
            if rr.status_code != 200 or is_waf(rr.content):
                print("child blocked", loc)
                continue
            child = re.findall(r"<loc>(.*?)</loc>", rr.text)
            print(
                f"CHILD {i} count={len(child)} "
                f"index={'sitemapindex' in rr.text.lower()} "
                f"urlset={'urlset' in rr.text.lower()}"
            )
            for u in child[:12]:
                print(" ", u)
            prefs: Counter[str] = Counter()
            for u in child:
                path = urlparse(u).path
                parts = [p for p in path.split("/") if p]
                key = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "/")
                prefs[key] += 1
            print(" prefixes", prefs.most_common(25))
    else:
        print("XML index blocked/error")

    # HTML sitemap
    r = get("https://www.invaluable.com/sitemap", sleep=4)
    (OUT / "sitemap.html").write_bytes(r.content)
    if r.status_code == 200 and not is_waf(r.content):
        links = analyze_html_links(r.text, "sitemap")
        (OUT / "sitemap_links.json").write_text(
            json.dumps([{"href": h, "text": t} for h, t in links], indent=2)
        )
        print("--- NAV ---")
        for href, text in links:
            low = text.lower()
            if any(
                x in low
                for x in [
                    "upcoming",
                    "auction house",
                    "gallery",
                    "artist",
                    "buy now",
                    "advanced",
                    "past",
                ]
            ):
                print(href, "=>", text)
        for needle in ["page=", "pagination", 'rel="next"', "/page/", "Load more", "?p="]:
            print("needle", needle, r.text.lower().count(needle.lower()))
        # structural snippets
        for pat in [
            r'(?is)<h1[^>]*>.*?</h1>',
            r'(?is)<section[^>]*sitemap[^>]*>.{0,600}',
            r'(?is)class="[^"]*sitemap[^"]*".{0,400}',
            r'(?is)<ul[^>]*class="[^"]*"[^>]*>.{0,500}',
        ]:
            m = re.search(pat, r.text)
            if m:
                print("SNIP", re.sub(r"\s+", " ", m.group(0))[:400])
    else:
        print("HTML sitemap blocked/error")

    # Auctions listing + pagination variants
    for url in [
        "https://www.invaluable.com/auctions/",
        "https://www.invaluable.com/auctions/?page=2",
        "https://www.invaluable.com/auctions/?p=2",
        "https://www.invaluable.com/auctions/?offset=50",
        "https://www.invaluable.com/auctions/?rows=50&page=2",
        "https://www.invaluable.com/auctions/?size=50&from=50",
    ]:
        rr = get(url, sleep=3)
        name = "auctions_" + re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(url).query or "root") + ".html"
        (OUT / name).write_bytes(rr.content)
        if rr.status_code == 200 and not is_waf(rr.content):
            cats = set(re.findall(r"/catalog/[A-Za-z0-9]+", rr.text))
            print(url, "catalogs", len(cats), "sample", list(cats)[:8])
            # look for pagination controls / JSON
            for needle in ["page=", "currentPage", "totalPages", "perPage", "rows=", "offset"]:
                print(" ", needle, rr.text.count(needle))
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', rr.text, re.S)
            if m:
                (OUT / (name + ".nextdata.json")).write_text(m.group(1)[:400000])
                print("  next_data", len(m.group(1)))
            # generic JSON state
            for pat in [
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
                r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});',
            ]:
                mm = re.search(pat, rr.text, re.S)
                if mm:
                    print("  found state blob", pat[:40], len(mm.group(1)))

    # Sample entity pages
    for url, name in [
        ("https://www.invaluable.com/catalog/FB7GQ4U6L5", "catalog_live.html"),
        ("https://www.invaluable.com/catalog/gbb4h4ahap", "catalog_past.html"),
        (
            "https://www.invaluable.com/auction-lot/heuer-abercrombie-fitch-co-stainless-steel-seafar-134-c-f38e67d2a8",
            "lot_sample.html",
        ),
        ("https://www.invaluable.com/auction-house/timeline-auctions-9mq71klbbn", "house_sample.html"),
    ]:
        rr = get(url, sleep=3)
        (OUT / name).write_bytes(rr.content)
        if rr.status_code == 200 and not is_waf(rr.content):
            title = re.search(r"<title>(.*?)</title>", rr.text, re.I | re.S)
            print(name, "title=", title.group(1).strip()[:160] if title else None)
            analyze_html_links(rr.text, name)


if __name__ == "__main__":
    main()
