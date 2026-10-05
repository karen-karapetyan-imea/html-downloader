"""Parse one Artsy HTML page into rows per table.

Artsy pages embed their GraphQL responses as `var __RELAY_HYDRATION_DATA__ = "<json string>"`
(JSON encoded twice). Artist pages carry one `artist` object; artwork pages carry `artworkResult`.
Everything the page loads later in the browser (artwork grid, auction results, shows) is NOT in
the saved HTML, so it can't be extracted here.

parse_page() returns:
    rows:    {table: [row, ...]}           rows to upsert
    replace: {table: (column, [values])}   child rows to delete first (they are re-inserted from `rows`)
"""
import base64
import html as htmllib
import json
import re

BASE = "https://www.artsy.net"
RELAY_RE = re.compile(r'var __RELAY_HYDRATION_DATA__ = ("(?:[^"\\]|\\.)*")', re.S)
CANONICAL_RE = re.compile(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"')
META_RE = re.compile(r'<meta[^>]*(?:name|property)="([^"]+)"[^>]*content="([^"]*)"')
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href="([^"]+)"')
YEAR_RE = re.compile(r"\b(\d{4})\b")


def clean(v):
    if not isinstance(v, str):
        return v
    v = v.replace("​", "").replace("\xa0", " ").strip()
    return v or None


def strip_html(v):
    return clean(htmllib.unescape(TAG_RE.sub("", v))) if v else None


def year_of(v):
    m = YEAR_RE.search(v or "")
    return int(m.group(1)) if m else None


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def abs_url(u):
    if not u:
        return None
    return BASE + u if u.startswith("/") else u


def slug_of(href):
    return href.rstrip("/").rsplit("/", 1)[-1] if href else None


def relay_internal_id(rid):
    """Relay ids are base64("Type:internalID")."""
    try:
        return base64.b64decode(rid + "=" * (-len(rid) % 4)).decode().split(":", 1)[1]
    except Exception:
        return None


def g(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_page(html):
    out = {"page_type": "unknown", "rows": {}, "replace": {}}
    c = CANONICAL_RE.search(html)
    out["canonical_url"] = c.group(1) if c else None
    meta = {}
    for k, v in META_RE.findall(html):
        meta.setdefault(k, htmllib.unescape(v))
    t = TITLE_RE.search(html)
    meta["title"] = clean(htmllib.unescape(t.group(1))) if t else None

    m = RELAY_RE.search(html)
    if not m:
        return out
    responses = {}
    for key, resp in json.loads(json.loads(m.group(1))):
        qid = json.loads(key).get("queryID", "")
        if qid != "buildAppRoutesQuery":            # site navigation menu, not page data
            responses[qid] = resp.get("json") or {}
    out["raw"] = responses
    errors = [e for r in responses.values() for e in (r.get("errors") or [])]
    out["graphql_errors"] = [{"message": e.get("message"), "path": e.get("path")} for e in errors] or None

    artist_resp = next((r for q, r in responses.items() if q.startswith("artistRoutes_ArtistApp")), None)
    artwork_resp = next((r for q, r in responses.items() if q.startswith("artworkRoutes_Artwork")), None)
    if artist_resp and g(artist_resp, "data", "artist"):
        _artist_page(out, artist_resp["data"]["artist"], meta)
    elif artwork_resp and g(artwork_resp, "data", "artworkResult", "slug"):
        _artwork_page(out, artwork_resp["data"]["artworkResult"], meta)
    return out


def add(out, table, row):
    out["rows"].setdefault(table, []).append(row)


def _artist_page(out, a, meta):
    out["page_type"] = "artist"
    aid = a.get("internalID")
    out["artist_id"] = aid
    bio = a.get("biographyBlurb") or {}
    credit = bio.get("credit")
    credit_href = HREF_RE.search(credit or "")
    cover = a.get("coverArtwork") or {}
    cover_img = cover.get("image") or {}
    add(out, "artists", {
        "artist_id": aid, "slug": a.get("slug"), "name": clean(a.get("name")), "url": abs_url(a.get("href")),
        "gender": clean(a.get("gender")), "nationality": clean(a.get("nationality")),
        "birthday": clean(a.get("birthday")), "deathday": clean(a.get("deathday")),
        "birth_year": year_of(a.get("birthday")), "death_year": year_of(a.get("deathday")),
        "hometown": clean(a.get("hometown")),
        "nationality_and_dates": clean(a.get("formattedNationalityAndBirthday")),
        "alternate_names": [n for n in (a.get("alternateNames") or []) if clean(n)] or None,
        "awards": clean(a.get("awards")),
        "biography": clean(g(a, "biographyBlurbPlain", "text")), "biography_html": clean(bio.get("text")),
        "biography_credit": strip_html(credit),
        "biography_credit_url": abs_url(credit_href.group(1)) if credit_href else None,
        "follows_count": g(a, "counts", "follows"),
        "articles_count": g(a, "articlesConnection", "totalCount"),
        "cover_artwork_slug": cover.get("slug"),
        "cover_image_url": cover_img.get("src") or cover_img.get("large"),
        "cover_image_width": cover_img.get("width"), "cover_image_height": cover_img.get("height"),
        "meta_title": meta.get("title"), "meta_description": clean(meta.get("description")),
        "og_image_url": clean(meta.get("og:image")), "has_page": True,
    })

    children = ["artist_genes", "artist_representatives", "artist_insights", "artist_articles",
                "artist_notable_artworks"]
    out["replace"] = {t: ("artist_id", [aid]) for t in children}

    pos = 0
    for kind, key in (("category", "genes"), ("medium", "mediumGenes"), ("movement", "movementGenes")):
        for gene in a.get(key) or []:
            gid = gene.get("internalID") or relay_internal_id(gene.get("id") or "")
            if not gid:
                continue
            pos += 1
            add(out, "genes", {"gene_id": gid, "slug": gene.get("slug"), "name": clean(gene.get("name"))})
            add(out, "artist_genes", {"artist_id": aid, "gene_id": gid, "kind": kind, "position": pos})

    for pos, rep in enumerate(a.get("verifiedRepresentatives") or [], 1):
        p = rep.get("partner") or {}
        if not p.get("internalID"):
            continue
        add(out, "partners", {"partner_id": p["internalID"], "slug": slug_of(p.get("href")),
                              "name": clean(p.get("name")), "url": abs_url(p.get("href")),
                              "icon_url": g(p, "profile", "icon", "src2x", "src")})
        add(out, "artist_representatives", {"artist_id": aid, "partner_id": p["internalID"], "position": pos})

    for pos, ins in enumerate(a.get("insights") or [], 1):
        entities = [e for e in (ins.get("entities") or []) if clean(e)]
        label = clean(ins.get("label")) or ""
        if not entities and not ins.get("description") and " 0 " in label:
            continue                             # placeholder rows like "Solo show at 0 major institutions"
        add(out, "artist_insights", {"artist_id": aid, "kind": ins.get("kind"), "label": label or None,
                                     "description": clean(ins.get("description")),
                                     "entities": entities or None, "position": pos})

    for pos, edge in enumerate(g(a, "articlesConnection", "edges") or [], 1):
        n = edge.get("node") or {}
        if not n.get("internalID"):
            continue
        add(out, "articles", {"article_id": n["internalID"], "title": clean(n.get("title")),
                              "byline": clean(n.get("byline")), "url": abs_url(n.get("href")),
                              "published_at": n.get("publishedAt"),
                              "thumbnail_url": g(n, "thumbnailImage", "small", "src")})
        add(out, "artist_articles", {"artist_id": aid, "article_id": n["internalID"], "position": pos})

    if cover.get("slug"):
        add(out, "artworks", {"slug": cover["slug"], "artist_id": aid, "title": clean(cover.get("title")),
                              "date": clean(cover.get("date")), "year": year_of(cover.get("date")),
                              "url": abs_url(cover.get("href")), "image_url": cover_img.get("src"),
                              "image_width": cover_img.get("width"), "image_height": cover_img.get("height")})
    for pos, w in enumerate(a.get("notableArtworks") or [], 1):
        slug = slug_of(w.get("href"))
        if not slug:
            continue
        add(out, "artworks", {"slug": slug, "artist_id": aid, "title": clean(w.get("title")),
                              "date": clean(w.get("date")), "year": year_of(w.get("date")),
                              "url": abs_url(w.get("href"))})
        add(out, "artist_notable_artworks", {"artist_id": aid, "artwork_slug": slug, "position": pos})


def _artwork_page(out, w, meta):
    out["page_type"] = "artwork"
    slug = w["slug"]
    out["artwork_slug"] = slug
    out["replace"] = {t: ("artwork_slug", [slug])
                      for t in ("artwork_artists", "artwork_images", "artwork_edition_sets")}

    artist_ids = []
    for pos, ar in enumerate(w.get("artists") or [], 1):
        aid = ar.get("internalID") or relay_internal_id(ar.get("id") or "")
        if not aid:
            continue
        artist_ids.append(aid)
        add(out, "artists", {"artist_id": aid, "slug": ar.get("slug"), "name": clean(ar.get("name")),
                             "url": abs_url(ar.get("href")),
                             "nationality_and_dates": clean(ar.get("formattedNationalityAndBirthday")),
                             "follows_count": g(ar, "counts", "follows"),
                             "biography_html": clean(g(ar, "biographyBlurb", "text"))})
        add(out, "artwork_artists", {"artwork_slug": slug, "artist_id": aid, "position": pos})
    primary = relay_internal_id(g(w, "artist", "id") or "") or (artist_ids[0] if artist_ids else None)
    if primary and primary not in artist_ids:
        primary = artist_ids[0] if artist_ids else None

    p = w.get("partner") or {}
    if p.get("internalID"):
        add(out, "partners", {"partner_id": p["internalID"], "slug": p.get("slug"), "name": clean(p.get("name")),
                              "url": abs_url(p.get("href")), "icon_url": g(p, "profile", "icon", "url"),
                              "cities": p.get("cities") or None, "is_inquireable": p.get("isInquireable")})

    img = g(w, "image", "resized") or {}
    signals = w.get("collectorSignals") or {}
    add(out, "artworks", {
        "slug": slug, "artwork_id": w.get("internalID"), "artist_id": primary,
        "title": clean(w.get("title")), "date": clean(w.get("date")), "year": year_of(w.get("date")),
        "url": abs_url(w.get("href")), "artist_names": clean(w.get("artistNames")),
        "category": clean(w.get("category")), "medium": clean(w.get("medium")),
        "medium_type": clean(g(w, "mediumType", "name")),
        "attribution_class": clean(g(w, "attributionClass", "name")), "series": clean(w.get("series")),
        "dimensions_in": clean(g(w, "dimensions", "in")), "dimensions_cm": clean(g(w, "dimensions", "cm")),
        "width_cm": num(w.get("widthCm")), "height_cm": num(w.get("heightCm")),
        "depth_cm": num(w.get("depthCm")), "diameter_cm": num(w.get("diameterCm")),
        "is_edition": w.get("isEdition"), "edition_of": clean(w.get("editionOf")),
        "price_display": clean(w.get("priceListedDisplay")) or clean(g(w, "listPrice", "display")),
        "price_amount": num(g(w, "listPrice", "major")),
        "price_currency": clean(w.get("priceCurrency")) or clean(g(w, "listPrice", "currencyCode")),
        "sale_message": clean(w.get("saleMessage")), "availability": clean(w.get("availability")),
        "is_sold": w.get("isSold"), "is_acquireable": w.get("isAcquireable"),
        "is_offerable": w.get("isOfferable"), "is_inquireable": w.get("isInquireable"),
        "is_in_auction": w.get("isInAuction"), "is_biddable": w.get("isBiddable"),
        "is_framed": w.get("isFramed"), "framed_details": clean(g(w, "framed", "details")),
        "signature": clean(g(w, "signatureInfo", "details")),
        "has_certificate_of_authenticity": w.get("hasCertificateOfAuthenticity"),
        "condition_description": clean(w.get("conditionDescription")),
        "publisher": clean(w.get("publisher")), "manufacturer": clean(w.get("manufacturer")),
        "image_rights": clean(w.get("imageRights")), "provenance": clean(w.get("provenance")),
        "exhibition_history": clean(w.get("exhibitionHistory")), "literature": clean(w.get("literature")),
        "description_html": clean(w.get("descriptionHTML")),
        "additional_info_html": clean(w.get("additionalInformationHTML")),
        "shipping_origin": clean(w.get("shippingOrigin")), "shipping_info": clean(w.get("shippingInfo")),
        "pickup_available": w.get("pickupAvailable"),
        "price_includes_tax": clean(w.get("priceIncludesTaxDisplay")),
        "partner_id": p.get("internalID"), "visibility_level": clean(w.get("visibilityLevel")),
        "is_published": w.get("published"),
        "curators_pick": signals.get("curatorsPick"), "increased_interest": signals.get("increasedInterest"),
        "image_url": img.get("src"), "image_width": img.get("width"), "image_height": img.get("height"),
        "meta_title": clean(g(w, "meta", "title")) or meta.get("title"),
        "meta_description": clean(g(w, "meta", "description")), "has_detail": True,
    })

    for pos, im in enumerate(w.get("images") or [], 1):
        fb = im.get("resized") or im.get("fallback") or {}
        add(out, "artwork_images", {"artwork_slug": slug, "position": pos, "image_id": im.get("internalID"),
                                    "url": im.get("url"), "is_default": im.get("isDefault"),
                                    "width": fb.get("width"), "height": fb.get("height")})
    for pos, es in enumerate(w.get("editionSets") or [], 1):
        if not es.get("internalID"):
            continue
        add(out, "artwork_edition_sets", {
            "artwork_slug": slug, "edition_set_id": es["internalID"], "position": pos,
            "edition_of": clean(es.get("editionOf")), "sale_message": clean(es.get("saleMessage")),
            "dimensions_in": clean(g(es, "dimensions", "in")), "dimensions_cm": clean(g(es, "dimensions", "cm")),
            "is_acquireable": es.get("isAcquireable"), "is_offerable": es.get("isOfferable")})
