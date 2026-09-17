#!/usr/bin/env python3
"""Readonly probe of The Saleroom Price Guide: Algolia indexes, APIs, URL rules, auth.

Writes output/saleroom_probe/probe_report.json with go/no-go for historical discovery.
Does not bypass paywalls or robots-disallowed paths.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

BASE = "https://www.the-saleroom.com"
KNOWN_LIVE_APP = "2125HT9M59"
KNOWN_LIVE_KEY = "b1f48e75f522cc67ff7751f1abd75458"
KNOWN_LIVE_INDEX = "lots_sr_en"

ART_PRICE_GUIDE_PATHS = (
    "/en-gb/price-guide",
    "/en-gb/price-guide/fine-art",
    "/en-gb/price-guide/decorative-art",
    "/en-gb/price-guide/asian-art",
    "/en-gb/price-guide/collectables",
    "/en-gb/price-guide/antiquities",
)

USER_AGENT = (
    # Chrome UA often gets HTTP 405 from CloudFront on Price Guide HTML;
    # Googlebot is allowed (same pattern as earlier sitemap probes).
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

ALGOLIA_RE = {
    "app_id": re.compile(
        r'(?:applicationId|appId|ALGOLIA_APP_ID)\s*[:=]\s*["\']([A-Z0-9]{8,12})["\']',
        re.I,
    ),
    "api_key": re.compile(
        r'(?:apiKey|searchApiKey|ALGOLIA_API_KEY)\s*[:=]\s*["\']([a-f0-9]{32})["\']',
        re.I,
    ),
    "index": re.compile(
        r'(?:indexName|indexes?)\s*[:=]\s*\[?\s*["\']([A-Za-z0-9_\-]+)["\']',
        re.I,
    ),
    "algoliasearch": re.compile(
        r'algoliasearch\(\s*["\']([A-Z0-9]{8,12})["\']\s*,\s*["\']([a-f0-9]{32})["\']',
        re.I,
    ),
}

ARCHIVELOT_RE = re.compile(r"/archivelot/[^\"'\s<>]+", re.I)
PRICE_GUIDE_LOT_RE = re.compile(
    r'href=["\']([^"\']*(?:price-guide|sold|results)[^"\']*)["\']',
    re.I,
)
LOT_CATALOGUE_RE = re.compile(
    r'href=["\']([^"\']*/auction-catalogues/[^"\']*/lot-[0-9a-fA-F-]{36}/?)["\']',
    re.I,
)
API_RE = re.compile(
    r'["\']((?:https?:)?//[^"\']*(?:api|search|priceguide|price-guide)[^"\']*)["\']',
    re.I,
)


def load_first_proxy() -> str | None:
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


def is_waf(content: bytes) -> bool:
    sample = content[:8192].lower()
    return any(
        kw in sample
        for kw in (
            b"awswaf",
            b"human verification",
            b"captcha-container",
            b"challenge.js",
            b"gokuprops",
        )
    )


def get_bytes(url: str, *, proxy: str | None = None, timeout: float = 60.0) -> tuple[int, bytes, str]:
    """Return (status, body, backend). Prefer curl_cffi; fall back to urllib."""
    time.sleep(0.8)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": f"{BASE}/en-gb",
    }
    try:
        from curl_cffi import requests as crequests

        kwargs: dict[str, Any] = {
            "impersonate": "chrome131",
            "timeout": timeout,
            "headers": headers,
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        try:
            resp = crequests.get(url, **kwargs)
        except Exception:
            if not proxy:
                raise
            kwargs.pop("proxies", None)
            resp = crequests.get(url, **kwargs)
        # CloudFront often 405s Chrome TLS fingerprint on Price Guide; fall back.
        if int(resp.status_code) == 405:
            raise RuntimeError("curl_cffi 405; try urllib Googlebot")
        return int(resp.status_code), bytes(resp.content), "curl_cffi"
    except Exception as exc:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(req, timeout=timeout) as response:
                return int(response.status), response.read(), f"urllib({exc.__class__.__name__})"
        except urllib.error.HTTPError as http_exc:
            return int(http_exc.code), http_exc.read(), f"urllib_http({exc.__class__.__name__})"


def extract_algolia(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {
        "app_ids": [],
        "api_keys": [],
        "index_names": [],
        "pairs": [],
    }
    for m in ALGOLIA_RE["algoliasearch"].finditer(text):
        pair = f"{m.group(1)}:{m.group(2)}"
        if pair not in found["pairs"]:
            found["pairs"].append(pair)
        if m.group(1) not in found["app_ids"]:
            found["app_ids"].append(m.group(1))
        if m.group(2) not in found["api_keys"]:
            found["api_keys"].append(m.group(2))
    for m in ALGOLIA_RE["app_id"].finditer(text):
        if m.group(1) not in found["app_ids"]:
            found["app_ids"].append(m.group(1))
    for m in ALGOLIA_RE["api_key"].finditer(text):
        if m.group(1) not in found["api_keys"]:
            found["api_keys"].append(m.group(1))
    for m in ALGOLIA_RE["index"].finditer(text):
        name = m.group(1)
        if name not in found["index_names"]:
            found["index_names"].append(name)
    # Broader catch for lots_* / archive / priceguide index tokens in JS.
    for m in re.finditer(
        r'["\']((?:lots|archive|price|sold|pg|sr)_[A-Za-z0-9_\-]{2,40})["\']',
        text,
        re.I,
    ):
        if m.group(1) not in found["index_names"]:
            found["index_names"].append(m.group(1))
    return found


def script_srcs(html: str) -> list[str]:
    urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    out: list[str] = []
    for raw in urls:
        full = urljoin(BASE + "/", raw)
        if any(tok in full.lower() for tok in ("algolia", "chunk", "main", "vendor", "app")):
            out.append(full)
    return out[:40]


def algolia_request(
    app_id: str,
    api_key: str,
    index: str,
    *,
    method: str,
    filters: str = "",
    page: int = 0,
) -> dict[str, Any]:
    path = "browse" if method == "browse" else "query"
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/{path}"
    if method == "browse":
        body = {"params": urllib.parse.urlencode({"query": "", "filters": filters, "hitsPerPage": 2})}
    else:
        parts = [
            "query=",
            f"page={page}",
            "hitsPerPage=2",
        ]
        if filters:
            parts.append(f"filters={urllib.parse.quote(filters, safe=':\"() ')}")
        body = {"params": "&".join(parts)}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key": api_key,
            "User-Agent": USER_AGENT,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=45) as resp:
            raw = resp.read()
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        return {
            "status": int(exc.code),
            "error": exc.read().decode("utf-8", errors="replace")[:400],
        }
    except Exception as exc:
        return {"status": 0, "error": str(exc)}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"status": status, "error": "invalid json", "raw": raw[:200].decode("utf-8", "replace")}
    hits = payload.get("hits") if isinstance(payload, dict) else None
    sample = None
    if isinstance(hits, list) and hits:
        hit = hits[0]
        sample = {
            "objectID": hit.get("objectID"),
            "keys": sorted(hit.keys())[:40],
            "title": hit.get("title") or hit.get("lotTitle"),
            "auctioneerRef": hit.get("auctioneerRef"),
            "auctionRef": hit.get("auctionRef"),
            "url_hint": hit.get("url") or hit.get("lotUrl") or hit.get("permalink"),
        }
    return {
        "status": status,
        "nbHits": payload.get("nbHits") if isinstance(payload, dict) else None,
        "nbPages": payload.get("nbPages") if isinstance(payload, dict) else None,
        "message": payload.get("message") if isinstance(payload, dict) else None,
        "sample_hit": sample,
    }


def robots_allows(path: str, robots_text: str) -> bool | None:
    """Naive Disallow check for User-agent: *."""
    if not robots_text:
        return None
    disallows: list[str] = []
    in_star = False
    for line in robots_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lower = stripped.lower()
        if lower.startswith("user-agent:"):
            agent = stripped.split(":", 1)[1].strip()
            in_star = agent == "*"
            continue
        if in_star and lower.startswith("disallow:"):
            rule = stripped.split(":", 1)[1].strip()
            if rule:
                disallows.append(rule)
    for rule in disallows:
        if rule == "/":
            return False
        # robots wildcards used by Saleroom: */archivelot*
        if "*" in rule:
            # convert simple *glob* to substring checks
            parts = [p for p in rule.split("*") if p]
            if parts and all(p in path for p in parts):
                return False
        elif path.startswith(rule):
            return False
    return True


def decide_go_nogo(report: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []

    indexes = set(report.get("algolia_discovered", {}).get("index_names") or [])
    historical_indexes = sorted(
        name
        for name in indexes
        if name != KNOWN_LIVE_INDEX
        and any(tok in name.lower() for tok in ("archive", "price", "sold", "pg", "hist", "result"))
    )
    paginate_ok = any(
        isinstance(row, dict)
        and row.get("status") == 200
        and isinstance(row.get("nbHits"), int)
        and row["nbHits"] > 0
        and name != KNOWN_LIVE_INDEX
        for name, methods in (report.get("algolia_probes") or {}).items()
        for method, row in (methods or {}).items()
    )
    paywall = bool(report.get("paywall_signals"))
    archivelot_disallow = report.get("robots", {}).get("archivelot_disallow")
    public_url_ok = bool(report.get("allowed_sold_url_samples"))

    if historical_indexes and paginate_ok and not paywall:
        decision = "go"
        reasons.append(
            f"Found paginatable non-live index(es): {historical_indexes}; "
            "no hard paywall signal on probe pages."
        )
    elif historical_indexes and paginate_ok and paywall:
        decision = "conditional_go"
        reasons.append(
            "Historical index responds, but Price Guide pages show subscription paywall; "
            "needs explicit approved auth before Phase C crawl of gated content."
        )
        blockers.append("subscription_paywall")
    else:
        decision = "no_go"
        if not historical_indexes:
            blockers.append("no_historical_algolia_index")
            reasons.append(
                f"Only live index {KNOWN_LIVE_INDEX!r} (and/or unrelated names) found; "
                "no dedicated Price Guide/archive index."
            )
        if not paginate_ok:
            blockers.append("no_public_pagination")
            reasons.append("Could not paginate a historical index with the public search key.")
        if paywall:
            blockers.append("subscription_paywall")
            reasons.append("Price Guide HTML indicates subscribe/login gating.")
        if archivelot_disallow:
            blockers.append("robots_archivelot")
            reasons.append("robots.txt disallows */archivelot* — do not enable those URLs.")

    if not public_url_ok and decision == "go":
        decision = "conditional_go"
        blockers.append("unclear_sold_url_template")
        reasons.append("Need a robots-allowed sold-lot URL template before wiring download.")

    return {
        "decision": decision,
        "historical_indexes": historical_indexes,
        "blockers": blockers,
        "reasons": reasons,
        "implement_phase_c": decision == "go",
    }


def main() -> None:
    proxy = load_first_proxy()
    report: dict[str, Any] = {
        "base": BASE,
        "proxy_used": bool(proxy),
        "known_live": {
            "app_id": KNOWN_LIVE_APP,
            "index": KNOWN_LIVE_INDEX,
        },
        "pages": {},
        "robots": {},
        "algolia_discovered": {"app_ids": [], "api_keys": [], "index_names": [], "pairs": []},
        "algolia_probes": {},
        "paywall_signals": [],
        "url_samples": {"archivelot": [], "catalogue_lot": [], "price_guide_hrefs": []},
        "api_hints": [],
        "allowed_sold_url_samples": [],
    }

    # robots
    status, body, backend = get_bytes(f"{BASE}/robots.txt", proxy=proxy)
    robots_text = body.decode("utf-8", errors="replace")
    (OUT / "robots.txt").write_text(robots_text, encoding="utf-8")
    report["robots"] = {
        "status": status,
        "backend": backend,
        "archivelot_disallow": "archivelot" in robots_text.lower(),
        "price_guide_ok": robots_allows("/en-gb/price-guide/fine-art", robots_text),
        "archivelot_ok": robots_allows("/en-gb/archivelot/example", robots_text),
    }

    blobs: list[str] = [robots_text]
    script_urls: list[str] = []

    for path in ART_PRICE_GUIDE_PATHS:
        url = urljoin(BASE, path)
        status, body, backend = get_bytes(url, proxy=proxy)
        label = path.strip("/").replace("/", "_") or "root"
        (OUT / f"{label}.html").write_bytes(body)
        text = body.decode("utf-8", errors="replace")
        waf = is_waf(body)
        lower = text.lower()
        paywall = any(
            tok in lower
            for tok in (
                "subscribe now",
                "click here to subscribe",
                "free one month trial",
                "price guide service",
                "sign in to view",
            )
        )
        if paywall:
            report["paywall_signals"].append({"url": url, "status": status})
        entry = {
            "url": url,
            "status": status,
            "size": len(body),
            "backend": backend,
            "waf": waf,
            "paywall": paywall,
            "hit_count_guess": None,
        }
        m = re.search(r"([\d,]+)\s+(?:price guide item|lots? that match|item\(s\))", text, re.I)
        if m:
            entry["hit_count_guess"] = int(m.group(1).replace(",", ""))
        report["pages"][label] = entry
        if status == 200 and not waf:
            blobs.append(text)
            script_urls.extend(script_srcs(text))
            report["url_samples"]["archivelot"].extend(ARCHIVELOT_RE.findall(text)[:20])
            report["url_samples"]["catalogue_lot"].extend(LOT_CATALOGUE_RE.findall(text)[:20])
            report["url_samples"]["price_guide_hrefs"].extend(PRICE_GUIDE_LOT_RE.findall(text)[:30])
            for api in API_RE.findall(text)[:20]:
                if api not in report["api_hints"]:
                    report["api_hints"].append(api)

    # Fetch a few JS bundles for credentials / indexes.
    seen_scripts: set[str] = set()
    for src in script_urls:
        if src in seen_scripts:
            continue
        seen_scripts.add(src)
        if len(seen_scripts) > 12:
            break
        status, body, backend = get_bytes(src, proxy=proxy)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", src.rsplit("/", 1)[-1])[:80]
        (OUT / f"js_{name}").write_bytes(body[:2_000_000])
        if status == 200:
            blobs.append(body.decode("utf-8", errors="replace"))

    # Homepage InstantSearch config often holds live creds — still scan for extra indexes.
    status, body, backend = get_bytes(f"{BASE}/en-gb", proxy=proxy)
    (OUT / "homepage_en-gb.html").write_bytes(body)
    if status == 200 and not is_waf(body):
        blobs.append(body.decode("utf-8", errors="replace"))
        script_urls.extend(script_srcs(body.decode("utf-8", errors="replace")))

    merged = {
        "app_ids": [],
        "api_keys": [],
        "index_names": [],
        "pairs": [],
    }
    for blob in blobs:
        found = extract_algolia(blob)
        for key in merged:
            for value in found[key]:
                if value not in merged[key]:
                    merged[key].append(value)
    report["algolia_discovered"] = merged

    # Always probe known live index + any discovered indexes.
    app_ids = merged["app_ids"] or [KNOWN_LIVE_APP]
    api_keys = merged["api_keys"] or [KNOWN_LIVE_KEY]
    indexes = list(dict.fromkeys([*merged["index_names"], KNOWN_LIVE_INDEX]))
    app_id = next((a for a in app_ids if a == KNOWN_LIVE_APP), app_ids[0])
    api_key = next((k for k in api_keys if k == KNOWN_LIVE_KEY), api_keys[0])

    art_filter = "masterCategoryCode:FIA"
    for index in indexes[:12]:
        report["algolia_probes"][index] = {
            "query": algolia_request(
                app_id, api_key, index, method="query", filters=art_filter
            ),
            "browse": algolia_request(
                app_id, api_key, index, method="browse", filters=art_filter
            ),
            "query_unfiltered": algolia_request(app_id, api_key, index, method="query"),
        }

    # Deduplicate URL samples
    for key in report["url_samples"]:
        report["url_samples"][key] = sorted(set(report["url_samples"][key]))[:40]

    # Allowed sold URL samples: catalogue lots only if not archivelot; price-guide detail if allowed.
    allowed: list[str] = []
    for href in report["url_samples"]["catalogue_lot"]:
        full = urljoin(BASE, href)
        if "archivelot" not in full.lower():
            allowed.append(full)
    for href in report["url_samples"]["price_guide_hrefs"]:
        full = urljoin(BASE, href)
        if "archivelot" in full.lower():
            continue
        if report["robots"].get("price_guide_ok") is False:
            continue
        allowed.append(full)
    report["allowed_sold_url_samples"] = sorted(set(allowed))[:30]

    report["decision"] = decide_go_nogo(report)

    out_path = OUT / "probe_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["decision"], indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
