#!/usr/bin/env python3
"""Readonly probe of Barnebys.com: WAF, Next.js chunks, Algolia keys, URL patterns."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

BASE = "https://www.barnebys.com"

try:
    from curl_cffi import requests as crequests

    BACKEND = "curl_cffi"

    def get(url: str, sleep: float = 1.5, *, proxy: str | None = None):
        time.sleep(sleep)
        kwargs: dict = {
            "impersonate": "chrome131",
            "timeout": 60,
            "headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{BASE}/",
            },
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        try:
            r = crequests.get(url, **kwargs)
        except Exception as exc:
            if proxy:
                print(f"proxy fail ({exc}); retry direct url={url[:80]}")
                kwargs.pop("proxies", None)
                r = crequests.get(url, **kwargs)
            else:
                raise
        print(f"status={r.status_code} size={len(r.content)} backend={BACKEND} url={url[:100]}")
        return r

except Exception as exc:  # noqa: BLE001
    import httpx

    BACKEND = f"httpx({exc})"

    def get(url: str, sleep: float = 1.5, *, proxy: str | None = None):
        time.sleep(sleep)
        proxies = proxy
        r = httpx.get(
            url,
            timeout=60,
            follow_redirects=True,
            proxy=proxies,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{BASE}/",
            },
        )
        print(f"status={r.status_code} size={len(r.content)} backend={BACKEND} url={url[:100]}")
        return r


def is_waf(content: bytes) -> bool:
    head = content[:2000].lower()
    return (
        b"azwaf" in head
        or b"azure waf" in head
        or b"afd_azwaf" in head
        or b"challenge.js" in head
        or b"awswaf" in head
    )


def load_first_proxy() -> str | None:
    """Return http://user:pass@host:port from project proxy.txt (host:port:user:pass)."""
    proxy_file = OUT.parents[1] / "proxy.txt"
    if not proxy_file.is_file():
        return None
    for line in proxy_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split(":", 3)
        if len(parts) != 4:
            continue
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    return None


ALGOLIA_PATTERNS = [
    re.compile(r'applicationId["\']?\s*[:=]\s*["\']([A-Z0-9]{8,12})["\']', re.I),
    re.compile(r'appId["\']?\s*[:=]\s*["\']([A-Z0-9]{8,12})["\']', re.I),
    re.compile(r'ALGOLIA_APP_ID["\']?\s*[:=]\s*["\']([A-Z0-9]{8,12})["\']', re.I),
    re.compile(r'NEXT_PUBLIC_ALGOLIA_APP_ID["\']?\s*[:=]\s*["\']([A-Z0-9]{8,12})["\']', re.I),
    re.compile(r'apiKey["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', re.I),
    re.compile(r'searchApiKey["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', re.I),
    re.compile(r'ALGOLIA_API_KEY["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', re.I),
    re.compile(r'NEXT_PUBLIC_ALGOLIA_SEARCH_KEY["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', re.I),
    re.compile(r'indexName["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]+)["\']', re.I),
    re.compile(r'algoliasearch\(\s*["\']([A-Z0-9]{8,12})["\']\s*,\s*["\']([a-f0-9]{32})["\']', re.I),
]


def extract_algolia(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {
        "app_ids": [],
        "api_keys": [],
        "index_names": [],
        "algoliasearch_pairs": [],
    }
    for m in ALGOLIA_PATTERNS[9].finditer(text):
        pair = f"{m.group(1)}:{m.group(2)}"
        if pair not in found["algoliasearch_pairs"]:
            found["algoliasearch_pairs"].append(pair)
        if m.group(1) not in found["app_ids"]:
            found["app_ids"].append(m.group(1))
        if m.group(2) not in found["api_keys"]:
            found["api_keys"].append(m.group(2))
    for pat in ALGOLIA_PATTERNS[:4]:
        for m in pat.finditer(text):
            if m.group(1) not in found["app_ids"]:
                found["app_ids"].append(m.group(1))
    for pat in ALGOLIA_PATTERNS[4:8]:
        for m in pat.finditer(text):
            if m.group(1) not in found["api_keys"]:
                found["api_keys"].append(m.group(1))
    for m in ALGOLIA_PATTERNS[8].finditer(text):
        name = m.group(1)
        if name not in found["index_names"] and "algolia" not in name.lower():
            # keep plausible index names
            if any(k in name.lower() for k in ("lot", "item", "auction", "product", "search", "prod", "en")):
                found["index_names"].append(name)
            elif len(name) > 3:
                found["index_names"].append(name)
    # broader indexName capture
    for m in re.finditer(r'["\']([a-z0-9_\-]{3,40})["\']', text):
        pass
    for m in re.finditer(
        r'(?:indexName|indexes?)\s*[:=]\s*\[?\s*["\']([A-Za-z0-9_\-]+)["\']',
        text,
        re.I,
    ):
        if m.group(1) not in found["index_names"]:
            found["index_names"].append(m.group(1))
    return found


def find_script_urls(html: str) -> list[str]:
    urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    out: list[str] = []
    for u in urls:
        full = urljoin(BASE + "/", u)
        if "_next/static" in full or "algolia" in full.lower():
            out.append(full)
    return out


def find_next_data(html: str) -> str | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return m.group(1) if m else None


def find_build_id(html: str, next_data: str | None) -> str | None:
    if next_data:
        try:
            data = json.loads(next_data)
            bid = data.get("buildId")
            if bid:
                return str(bid)
        except json.JSONDecodeError:
            pass
    m = re.search(r'/_next/static/([A-Za-z0-9_-]+)/_buildManifest\.js', html)
    return m.group(1) if m else None


def post_algolia(
    app_id: str,
    api_key: str,
    index: str,
    *,
    method: str = "browse",
    filters: str = "",
    page: int = 0,
) -> dict:
    import httpx

    path = "browse" if method == "browse" else "query"
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/{path}"
    payload: dict = {"query": "", "hitsPerPage": 5}
    if filters:
        payload["filters"] = filters
    if method == "query":
        payload["page"] = page
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        print(f"algolia {method} index={index} status={resp.status_code} size={len(resp.content)}")
        try:
            return {"status": resp.status_code, "body": resp.json()}
        except Exception:
            return {"status": resp.status_code, "body": resp.text[:500]}


def main() -> None:
    print("BACKEND_INIT", BACKEND)
    proxy = load_first_proxy()
    print("proxy", "yes" if proxy else "no")

    report: dict = {
        "backend": BACKEND,
        "proxy_used": bool(proxy),
        "pages": {},
        "algolia": {},
        "url_samples": {},
        "next_routes": {},
    }

    pages = {
        "robots": f"{BASE}/robots.txt",
        "homepage": f"{BASE}/",
        "auctions_all": f"{BASE}/auctions/all",
        "live_lot": f"{BASE}/auctions/lot/443468158/lockers",
        "result_lot": f"{BASE}/realized-prices/lot/443468158/lockers",
        "live_lot_alt": (
            f"{BASE}/auctions/lot/"
            "andy-warhol-1928-1987-marilyn-monroe-achenbach-edition-tTbkdDK-622510218"
        ),
        "sitemap": f"{BASE}/sitemap.xml",
    }

    all_text_blobs: list[str] = []
    script_urls: list[str] = []

    for label, url in pages.items():
        r = get(url, sleep=1.0, proxy=proxy)
        path = OUT / f"{label}.{'txt' if label == 'robots' else 'html'}"
        if label == "sitemap":
            path = OUT / "sitemap.xml"
        path.write_bytes(r.content)
        waf = is_waf(r.content)
        entry: dict = {
            "url": url,
            "status": r.status_code,
            "size": len(r.content),
            "waf": waf,
        }
        text = r.content.decode("utf-8", errors="replace")
        if not waf and r.status_code == 200:
            all_text_blobs.append(text)
            next_data = find_next_data(text)
            if next_data:
                (OUT / f"{label}.nextdata.json").write_text(next_data[:800_000], encoding="utf-8")
                entry["next_data_len"] = len(next_data)
                entry["build_id"] = find_build_id(text, next_data)
                all_text_blobs.append(next_data)
                # extract lot-like paths
                lots = re.findall(
                    r"/auctions/lot/[A-Za-z0-9\-_/]+",
                    next_data,
                )
                results = re.findall(
                    r"/realized-prices/lot/[A-Za-z0-9\-_/]+",
                    next_data,
                )
                entry["lot_paths_sample"] = list(dict.fromkeys(lots))[:20]
                entry["result_paths_sample"] = list(dict.fromkeys(results))[:20]
            for su in find_script_urls(text):
                if su not in script_urls:
                    script_urls.append(su)
            # __NEXT_DATA__ page props keys
            if next_data:
                try:
                    nd = json.loads(next_data)
                    props = nd.get("props", {}).get("pageProps", {})
                    entry["page_prop_keys"] = sorted(props.keys())[:40]
                except json.JSONDecodeError:
                    pass
        report["pages"][label] = entry

    # Fetch JS chunks (cap)
    algolia_agg: dict[str, list[str]] = {
        "app_ids": [],
        "api_keys": [],
        "index_names": [],
        "algoliasearch_pairs": [],
    }
    for i, su in enumerate(script_urls[:40]):
        rr = get(su, sleep=0.4, proxy=proxy)
        name = f"chunk_{i}_{Path(urlparse(su).path).name}"
        (OUT / name).write_bytes(rr.content[:2_000_000])
        if rr.status_code != 200 or is_waf(rr.content):
            continue
        text = rr.content.decode("utf-8", errors="replace")
        all_text_blobs.append(text)
        found = extract_algolia(text)
        for k, vals in found.items():
            for v in vals:
                if v not in algolia_agg[k]:
                    algolia_agg[k].append(v)
        if found["app_ids"] or found["api_keys"] or found["algoliasearch_pairs"]:
            print("ALGOLIA HIT in", name, found)

    # Also scan homepage HTML for inline
    for blob in all_text_blobs:
        found = extract_algolia(blob)
        for k, vals in found.items():
            for v in vals:
                if v not in algolia_agg[k]:
                    algolia_agg[k].append(v)

    # Extra patterns: x-algolia headers / hosts in JS
    for blob in all_text_blobs:
        for m in re.finditer(r"([A-Z0-9]{10})-dsn\.algolia\.net", blob):
            if m.group(1) not in algolia_agg["app_ids"]:
                algolia_agg["app_ids"].append(m.group(1))
        for m in re.finditer(r"algolia\.net/1/indexes/([A-Za-z0-9_\-]+)", blob):
            if m.group(1) not in algolia_agg["index_names"]:
                algolia_agg["index_names"].append(m.group(1))

    report["algolia"]["candidates"] = algolia_agg
    report["script_urls"] = script_urls[:40]

    # Try Algolia browse/query with discovered keys
    tests: list[dict] = []
    app_ids = list(algolia_agg["app_ids"])
    api_keys = list(algolia_agg["api_keys"])
    for pair in algolia_agg["algoliasearch_pairs"]:
        app, key = pair.split(":", 1)
        if app not in app_ids:
            app_ids.append(app)
        if key not in api_keys:
            api_keys.append(key)

    indexes = list(algolia_agg["index_names"]) or [
        "lots",
        "items",
        "auctions",
        "products",
        "prod_lots",
        "lots_en",
        "items_en",
        "barnebys",
        "objects",
        "search",
    ]

    for app_id in app_ids[:3]:
        for api_key in api_keys[:5]:
            for index in indexes[:15]:
                for method in ("browse", "query"):
                    try:
                        result = post_algolia(app_id, api_key, index, method=method)
                    except Exception as exc:  # noqa: BLE001
                        result = {"status": -1, "body": str(exc)}
                    hit_keys = []
                    sample_hit = None
                    body = result.get("body")
                    if isinstance(body, dict) and body.get("hits"):
                        sample_hit = body["hits"][0]
                        hit_keys = sorted(sample_hit.keys())
                        (OUT / f"algolia_{method}_{index}_sample.json").write_text(
                            json.dumps(body, indent=2, ensure_ascii=False)[:200_000],
                            encoding="utf-8",
                        )
                    tests.append(
                        {
                            "app_id": app_id,
                            "api_key_prefix": api_key[:6],
                            "index": index,
                            "method": method,
                            "status": result.get("status"),
                            "hit_keys": hit_keys,
                            "nbHits": body.get("nbHits") if isinstance(body, dict) else None,
                            "message": (
                                body.get("message") if isinstance(body, dict) else None
                            ),
                            "sample_objectID": (
                                sample_hit.get("objectID") if sample_hit else None
                            ),
                        }
                    )
                    if hit_keys:
                        print("SUCCESS", method, index, hit_keys[:20])
                        # stop early on first success path — still record a few
                        report["algolia"]["working"] = {
                            "app_id": app_id,
                            "api_key": api_key,
                            "index": index,
                            "method": method,
                            "hit_keys": hit_keys,
                            "sample_hit": sample_hit,
                        }
                        break
                if report["algolia"].get("working"):
                    break
            if report["algolia"].get("working"):
                break
        if report["algolia"].get("working"):
            break

    report["algolia"]["tests"] = tests

    # URL pattern samples from HTML
    combined = "\n".join(all_text_blobs)
    report["url_samples"] = {
        "auctions_lot_id_slug": list(
            dict.fromkeys(
                re.findall(r"/auctions/lot/(\d+)/([A-Za-z0-9\-]+)", combined)
            )
        )[:30],
        "auctions_lot_slug_form": list(
            dict.fromkeys(
                re.findall(
                    r"/auctions/lot/([A-Za-z0-9\-]+-\d+)",
                    combined,
                )
            )
        )[:30],
        "realized_lot_id_slug": list(
            dict.fromkeys(
                re.findall(
                    r"/realized-prices/lot/(\d+)/([A-Za-z0-9\-]+)",
                    combined,
                )
            )
        )[:30],
    }

    (OUT / "probe_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print("WROTE", OUT / "probe_report.json")
    print("ALGOLIA candidates", json.dumps(algolia_agg, indent=2))
    if report["algolia"].get("working"):
        w = report["algolia"]["working"]
        print(
            "WORKING",
            w["app_id"],
            w["index"],
            w["method"],
            w["hit_keys"][:25],
        )


if __name__ == "__main__":
    main()
