"""Parse one Artfinder HTML page into rows per table.

All data comes from the Next.js state (<script id="__NEXT_DATA__">). Page types:
artwork pages (/product/...), artist pages (/artist/...) and art listing pages (/art/...).

parse_page() returns:
    rows:    {table: [row, ...]}           rows to upsert
    replace: {table: (column, [values])}   child rows to delete first (re-inserted from `rows`)
"""
import html as htmllib
import json
import re

BASE = "https://www.artfinder.com"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")
DIMS_RE = re.compile(r"([\d.]+)\s*x\s*([\d.]+)(?:\s*x\s*([\d.]+))?\s*cm\s*\((\w+)", re.I)
CURRENCIES = ("gbp", "usd", "eur", "cad", "aud")
DROP_PROPS = {"_nextI18Next", "initialReduxState", "editorsPicks", "initOptions", "initSort",
              "initSelectedOptions", "initSelectedSort", "initSelectedPage", "initSelectedCollection"}


def clean(v):
    if not isinstance(v, str):
        return v
    v = re.sub(r"\s+", " ", v.replace("​", "")).strip()
    return v if v and v != "None" else None


def strip_html(v):
    if not v:
        return None
    v = re.sub(r"</p>\s*<p[^>]*>|<br\s*/?>", "\n", v)
    return clean(htmllib.unescape(TAG_RE.sub(" ", v)).replace(" \n ", "\n")) if v else None


def num(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(float(v)) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def fix_url(u):
    """Artfinder emits artist URLs like '.../artist/name}/'."""
    return u.replace("}", "") if u else None


def add(out, table, row):
    out["rows"].setdefault(table, []).append(row)


def parse_page(html):
    out = {"page_type": "unknown", "rows": {}, "replace": {}}
    c = CANONICAL_RE.search(html)
    out["canonical_url"] = c.group(1) if c else None
    m = NEXT_DATA_RE.search(html)
    if not m:
        return out
    nd = json.loads(m.group(1))
    props = (nd.get("props") or {}).get("pageProps") or {}
    page = nd.get("page") or ""
    out["raw"] = {k: v for k, v in props.items() if k not in DROP_PROPS}
    # Exact artist coordinates (and a map link built from them) are not kept anywhere.
    if isinstance(out["raw"].get("artist"), dict):
        out["raw"]["artist"] = {k: v for k, v in out["raw"]["artist"].items() if k != "lat_long"}
    bio_raw = (((out["raw"].get("initArtistPageInfo") or {}).get("about")) or {}).get("biography")
    if isinstance(bio_raw, dict):
        bio_raw.pop("location_map", None)

    rates = (((props.get("initialReduxState") or {}).get("currencyRates") or {}).get("data")) or {}
    for base, quotes in rates.items():
        for quote, rate in (quotes or {}).items():
            add(out, "currency_rates", {"base": base, "quote": quote, "rate": rate})

    if page.startswith("/product/") and props.get("product"):
        _artwork_page(out, props)
    elif page.startswith("/artist/") and props.get("artist"):
        _artist_page(out, props)
    elif page.startswith("/art/"):
        out["page_type"] = "listing"
        for item in ((props.get("initialProductSearchResponse") or {}).get("results")) or []:
            _card(out, item)
    return out


def artist_common(a):
    rd = a.get("review_data") or {}
    stars = {b.get("stars"): b.get("reviews") for b in rd.get("seller_rating_breakdown") or []}
    return {
        "artist_id": to_int(a.get("id")), "username": clean(a.get("username")), "slug": clean(a.get("slug")),
        "name": clean(a.get("name")), "url": fix_url(a.get("url")),
        "country": clean(a.get("country")), "country_code": clean(a.get("country_code")),
        "intro": strip_html(a.get("intro")), "intro_html": clean(a.get("intro")),
        "avatar_url": clean(a.get("avatar_url")),
        "website_url": clean(a.get("website_url")), "facebook_url": clean(a.get("facebook_url")),
        "instagram_username": clean(a.get("instagram_username")),
        "pinterest_username": clean(a.get("pinterest_username")),
        "twitter_username": clean(a.get("twitter_username")),
        "review_count": to_int(rd.get("review_count")), "seller_rating": num(rd.get("seller_rating")),
        "listing_rating": num(rd.get("listing_rating")),
        "communication_rating": num(rd.get("communication_rating")),
        "delivery_rating": num(rd.get("delivery_rating")),
        **({f"reviews_{s}_star": stars.get(s) for s in range(1, 6)} if rd.get("review_count") else {}),
    }


def _card(out, it):
    """Artwork card as used on artist shop grids and art listing pages."""
    wid = to_int(it.get("id"))
    if not wid:
        return
    aid = to_int(it.get("artist_id"))
    if aid:
        add(out, "artists", {"artist_id": aid, "slug": clean(it.get("artist_slug")),
                             "name": clean(it.get("artist_name")),
                             "url": f"{BASE}/artist/{it['artist_slug']}/" if it.get("artist_slug") else None})
    prices = it.get("prices") or {}
    pre = it.get("original_prices") or {}
    dims = it.get("dimensions") or {}
    cm = dims.get("units") == "cm"
    img = (it.get("images") or [{}])[0]
    le = it.get("le_prints") or {}
    add(out, "artworks", {
        "artwork_id": wid, "artist_id": aid, "name": clean(it.get("name")), "slug": clean(it.get("slug")),
        "url": f"{BASE}/product/{it['slug']}/" if it.get("slug") else None,
        "category_slug": clean(it.get("category_slug")), "category_name": clean(it.get("category_name")),
        "style_slug": clean(it.get("style_slug")), "subject_slug": clean(it.get("subject_slug")),
        "quantity": to_int(it.get("quantity")), "is_in_stock": it.get("is_in_stock"), "is_new": it.get("is_new"),
        "width_cm": num(dims.get("width")) if cm else None, "height_cm": num(dims.get("height")) if cm else None,
        "depth_cm": num(dims.get("depth")) if cm else None, "dimensions_type": clean(dims.get("type")),
        **{f"price_{c}": num(prices.get(c.upper())) for c in CURRENCIES},
        "pre_sale_price_usd": num(pre.get("USD")), "pre_sale_price_gbp": num(pre.get("GBP")),
        "in_sale": True if pre else None, "average_colour": clean(it.get("average_colour")),
        "has_limited_edition_prints": True if le.get("editions") else None,
        "image_url": img.get("path"), "image_width": img.get("width"), "image_height": img.get("height"),
    })
    for ed in le.get("editions") or []:
        d, p = ed.get("dimensions") or {}, ed.get("prices") or {}
        add(out, "artwork_print_editions", {
            "artwork_id": wid, "edition_id": to_int(ed.get("id")), "slug": clean(ed.get("slug")),
            "edition_of": to_int(ed.get("edition_of")), "quantity": to_int(ed.get("quantity")),
            "width": num(d.get("width")), "height": num(d.get("height")), "depth": num(d.get("depth")),
            "units": d.get("units"), "price_gbp": num(p.get("GBP")), "price_usd": num(p.get("USD")),
            "price_eur": num(p.get("EUR"))})
    if le.get("editions"):
        out["replace"].setdefault("artwork_print_editions", ("artwork_id", []))[1].append(wid)


def _artwork_page(out, props):
    out["page_type"] = "artwork"
    p = props["product"]
    wid = to_int(p.get("id"))
    a = props.get("artist") or {}
    aid = to_int(p.get("artist_id") or a.get("id"))
    out["artwork_id"], out["artist_id"] = wid, aid
    if aid:
        add(out, "artists", dict(artist_common(a), artist_id=aid))

    pricing = p.get("pricing") or {}
    dm = DIMS_RE.search(p.get("dimensions_text_cm") or "")
    imgs = p.get("images") or []
    main = imgs[0] if imgs else {}
    add(out, "artworks", {
        "artwork_id": wid, "artist_id": aid, "name": clean(p.get("name")), "slug": clean(p.get("slug")),
        "url": clean(p.get("full_url")) or props.get("isCanonicalLink") and out["canonical_url"],
        "category_slug": clean(p.get("category_slug")), "category_name": clean(p.get("category_full_name")),
        "style_slug": clean(p.get("style_slug")), "style_name": clean(p.get("style_name")),
        "subject_slug": clean(p.get("subject_slug")), "subject_name": clean(p.get("subject_name")),
        "description": clean(p.get("description")), "year_made": to_int(p.get("year_made")),
        "is_unique": p.get("unique"), "edition_size": to_int(p.get("edition")),
        "quantity": to_int(p.get("quantity")), "is_in_stock": p.get("is_in_stock"), "is_new": p.get("is_new"),
        "is_framed": bool(p.get("is_framed")), "framed_text": clean(p.get("framed_text")),
        "is_ready_to_hang": bool(p.get("is_ready_to_hang")), "substrate": clean(p.get("substrate")),
        "materials": clean(p.get("materials")), "signature": clean(p.get("signature_type_name")),
        "tags": [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()] or None,
        "dimensions_cm": clean(p.get("dimensions_text_cm")), "dimensions_in": clean(p.get("dimensions_text_in")),
        "width_cm": num(dm.group(1)) if dm else num(p.get("unframed_dimensions_width")),
        "height_cm": num(dm.group(2)) if dm else num(p.get("unframed_dimensions_height")),
        "depth_cm": num(dm.group(3)) if dm else None,
        "dimensions_type": dm.group(4).lower() if dm else None,
        "shipping_width": num(p.get("shipping_width")), "shipping_height": num(p.get("shipping_height")),
        "shipping_depth": num(p.get("shipping_depth")), "shipping_weight": num(p.get("shipping_weight")),
        "original_currency": clean(p.get("original_currency")), "original_amount": num(p.get("original_currency_amount")),
        **{f"price_{c}": num((pricing.get(c.upper()) or {}).get("current_amount")) for c in CURRENCIES},
        "pre_sale_price_usd": num((pricing.get("USD") or {}).get("original_amount")),
        "pre_sale_price_gbp": num((pricing.get("GBP") or {}).get("original_amount")),
        "in_sale": bool(p.get("in_sale")), "discount_amount": num(p.get("discount_amount")),
        "sale_ends_at": clean(p.get("sale_ends_datetime")),
        "accepts_offers": bool(p.get("accepts_offers")),
        "artist_accepts_commissions": p.get("artist_accepts_commissions"),
        "interest_count": to_int(p.get("interest_count")), "review_count": to_int(p.get("review_count")),
        "seller_rating": num(p.get("summary_seller_rating")),
        "has_video": p.get("has_video_feature"), "video_link": clean(p.get("video_link")),
        "verified_advertising_safe": p.get("verified_advertising_safe"),
        "provider_managed_shipping": bool(props.get("isProviderManagedShipping")),
        "managed_shipping_table_id": to_int(p.get("managed_shipping_table_id")),
        "holiday_message": clean(p.get("holiday_user_message")),
        "image_url": main.get("url"), "image_width": main.get("width"), "image_height": main.get("height"),
        "has_detail": True,
    })
    out["replace"].update({t: ("artwork_id", [wid]) for t in
                           ("artwork_images", "artwork_categories", "artwork_featured_collections")})
    for pos, im in enumerate(imgs):
        add(out, "artwork_images", {"artwork_id": wid, "position": pos, "image_type": clean(im.get("image_type")),
                                    "url": im.get("url"), "retina_url": im.get("retina_url"),
                                    "thumbnail_url": im.get("thumbnail_url"),
                                    "width": im.get("width"), "height": im.get("height")})
    for pos, cat in enumerate(p.get("categories") or [], 1):
        add(out, "artwork_categories", {"artwork_id": wid, "position": pos, "slug": clean(cat.get("slug")),
                                        "name": clean(cat.get("name")), "full_name": clean(cat.get("full_name")),
                                        "depth": to_int(cat.get("depth")), "parent_slug": clean(cat.get("parent_slug"))})
    for pos, fc in enumerate(p.get("featured_collections") or [], 1):
        add(out, "artwork_featured_collections", {"artwork_id": wid, "position": pos, "slug": clean(fc.get("slug")),
                                                  "title": clean(fc.get("title")), "url": fc.get("url"),
                                                  "description": clean(fc.get("description"))})


def _artist_page(out, props):
    out["page_type"] = "artist"
    a = props["artist"]
    aid = to_int(a.get("id"))
    out["artist_id"] = aid
    info = props.get("initArtistPageInfo") or {}
    about = info.get("about") or {}
    bio = about.get("biography") or {}
    contact = about.get("contact_info") or {}
    counts = {o.get("model"): (o.get("buckets") or {}).get("true") for o in props.get("initOptions") or []}
    lat = lng = None
    if a.get("lat_long"):
        try:
            lat, lng = (round(float(x), 1) for x in a["lat_long"].split(","))
        except ValueError:
            pass
    add(out, "artists", dict(
        artist_common(a), provider_id=to_int(a.get("provider_id")), provider_slug=clean(a.get("provider_slug")),
        provider_display_name=clean(a.get("provider_display_name")), approx_lat=lat, approx_lng=lng,
        biography=strip_html(bio.get("text")), cover_url=clean(a.get("cover_url")),
        joined_at=clean(a.get("joined_at")), followers_count=to_int(a.get("favourite_count")),
        artworks_for_sale=to_int(a.get("artworks_for_sale")), artworks_total=to_int(props.get("initCount")),
        count_on_sale=counts.get("in_sale"), count_ready_to_hang=counts.get("framed_or_ready_to_hang"),
        count_in_stock=counts.get("availability"), count_exclusive=counts.get("is_exclusive"),
        accepts_commissions=a.get("accepts_commissions"), has_active_subscription=a.get("has_active_subscription"),
        is_represented_by_gallery=bool(a.get("is_represented_by_gallery")), has_me_at_work=a.get("me_at_work"),
        in_sale=bool(a.get("in_sale")), sale_discount_amount=num(a.get("sale_discount_amount")),
        website_url=clean(contact.get("website")) or clean(a.get("website_url")), has_page=True))

    children = ["artist_social_links", "artist_awards", "artist_education", "artist_events",
                "artist_featured_in", "artist_collections"]
    out["replace"].update({t: ("artist_id", [aid]) for t in children})
    for pos, s in enumerate(contact.get("social_media") or [], 1):
        add(out, "artist_social_links", {"artist_id": aid, "position": pos, "platform": clean(s.get("platform")),
                                         "url": clean(s.get("url"))})
    for pos, aw in enumerate(about.get("awards") or [], 1):
        add(out, "artist_awards", {"artist_id": aid, "position": pos, "year": clean(str(aw.get("year") or "")),
                                   "title": clean(aw.get("title")), "description": clean(aw.get("description"))})
    for pos, ed in enumerate(about.get("education") or [], 1):
        add(out, "artist_education", {"artist_id": aid, "position": pos, "school": clean(ed.get("school")),
                                      "years": clean(str(ed.get("years") or ""))})
    for kind in ("upcoming", "previous"):
        for pos, ev in enumerate(about.get(f"{kind}_events") or [], 1):
            add(out, "artist_events", {"artist_id": aid, "kind": kind, "position": pos, "title": clean(ev.get("title")),
                                       "venue": clean(ev.get("venue")), "dates": clean(ev.get("dates")),
                                       "description": clean(ev.get("description"))})
    for pos, f in enumerate(info.get("featured_in") or [], 1):
        add(out, "artist_featured_in", {"artist_id": aid, "position": pos, "type": clean(f.get("type")),
                                        "label": clean(f.get("label")), "title": clean(f.get("title")),
                                        "description": strip_html(f.get("description")),
                                        "preview_text": clean(f.get("preview_text")), "link": f.get("link"),
                                        "image_path": f.get("image_path")})
    for pos, col in enumerate(((props.get("initCollections") or {}).get("values")) or [], 1):
        add(out, "artist_collections", {"artist_id": aid, "position": pos, "collection_id": col.get("id"),
                                        "name": clean(col.get("name"))})
    for item in props.get("initItems") or []:
        _card(out, item)
