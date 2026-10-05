"""Parse one Saatchi Art HTML page into structured rows.

Every page embeds its full Next.js state in <script id="__NEXT_DATA__">; all data comes
from there (regex + json, no HTML parsing needed, which keeps it fast at scale).
Two page types exist: artist profiles and artwork detail pages.
"""
import html as htmllib
import json
import re
from datetime import datetime, timezone

BASE = "https://www.saatchiart.com"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
CANONICAL_RE = re.compile(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"|<link[^>]+href="([^"]+)"[^>]+rel="canonical"')
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
PDP_URL_RE = re.compile(r"/(?:art|print)/([^/]+)/(\d+)/(\d+)/")
PROFILE_ID_RE = re.compile(r"/account/profile/(\d+)")


def clean(v):
    if v is None:
        return None
    if not isinstance(v, str):
        return v
    v = v.replace("​", "").replace("\xa0", " ").strip()
    return v or None


def usd(cents):
    """Source money is in cents; 0 / missing -> None."""
    try:
        return round(float(cents) / 100, 2) if cents not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def ts(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def abs_url(u):
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    return BASE + u if u.startswith("/") else u


def bool_or_none(v):
    return None if v is None else bool(v)


def str_list(v):
    return [clean(x) for x in v if clean(x)] if isinstance(v, list) else []


def badges_of(items):
    return [{"title": clean(b.get("title")), "description": clean(b.get("description")),
             "image_url": abs_url(b.get("image"))} for b in items or [] if clean(b.get("title"))]


def parse_page(html):
    out = {"page_type": "unknown", "artworks": [], "tags": [], "images": [], "products": [],
           "options": [], "region_prices": [], "rates": []}
    c = CANONICAL_RE.search(html)
    out["canonical_url"] = (c.group(1) or c.group(2)) if c else None
    m = NEXT_DATA_RE.search(html)
    if not m:
        # No embedded state: keep what the head gives us.
        pid = PROFILE_ID_RE.search(out["canonical_url"] or "")
        t = TITLE_RE.search(html)
        if pid:
            out["page_type"] = "artist_profile"
            out["partial"] = True
            name = htmllib.unescape(t.group(1)).split("|")[0].strip() if t else None
            out["artist"] = {"artist_id": int(pid.group(1)), "full_name": name,
                             "profile_url": out["canonical_url"]}
            out["artist_id"] = int(pid.group(1))
        return out

    nd = json.loads(m.group(1))
    props = nd["props"]["pageProps"]
    ip = props.get("initialProps") or {}
    state = props.get("initialState") or {}
    page = state.get("page") or {}
    data = page.get("data") or {}
    meta = ip.get("metaData") or {}
    shared = state.get("shared") or {}
    server_ms = shared.get("serverTimestampInMilliseconds")
    out["crawled_at"] = ts(server_ms / 1000) if server_ms else None
    out["raw"] = {"initialProps": ip, "page": page}

    as_of = out["crawled_at"][:10] if out["crawled_at"] else None
    currencies = ((shared.get("locale") or {}).get("availableCurrencies") or {}).values()
    out["rates"] = [{"as_of_date": as_of, "currency_code": r.get("currencyCode"),
                     "currency_name": r.get("currencyName"), "rate_per_usd": r.get("exchangeRate")}
                    for r in currencies if as_of and r.get("currencyCode")]


    if "accountData" in data:
        _parse_profile(out, data["accountData"], meta)
    elif "artwork" in data:
        _parse_artwork(out, data, meta, ip.get("dataLayer") or {})
    return out


def _add_tags(out, artwork_id, kind, names):
    for pos, name in enumerate(dict.fromkeys(str_list(names)), 1):
        out["tags"].append({"artwork_id": artwork_id, "kind": kind, "name": name, "position": pos})


def _parse_profile(out, a, meta):
    out["page_type"] = "artist_profile"
    aid = to_int(a.get("userId"))
    out["artist_id"] = aid
    social = a.get("socialLinks") or {}
    about = a.get("aboutArtist") or {}
    rep = a.get("representation") or {}
    hero = a.get("hero") or {}
    username = clean(a.get("userName"))
    first, last = clean(a.get("firstName")), clean(a.get("lastName"))
    out["artist"] = {
        "artist_id": aid, "username": username, "first_name": first, "last_name": last,
        "full_name": clean(" ".join(x for x in (first, last) if x)) or clean(a.get("displayName")),
        "profile_url": f"{BASE}/{username}" if username else meta.get("canonical"),
        "account_type": clean(a.get("userAccountType")),
        "avatar_url": abs_url(clean(a.get("avatar"))), "avatar_large_url": abs_url(clean(a.get("avatarLarge"))),
        "hero_image_url": abs_url(clean(hero.get("largeImage"))),
        "hero_image_small_url": abs_url(clean(hero.get("smallImage"))),
        "city": clean(a.get("city")), "state": clean(a.get("state")), "zipcode": clean(a.get("zipcode")),
        "country": clean(a.get("country")), "country_code": clean(a.get("countryCode")),
        "representation_city": clean(rep.get("city")), "representation_state": clean(rep.get("state")),
        "representation_country": clean(rep.get("country")),
        "joined_at": ts(a.get("joinedDate")), "followers_count": to_int(a.get("followersTotal")),
        "artworks_total": to_int(a.get("artworksTotal")), "can_index": bool_or_none(a.get("canIndex")),
        "reasons_not_allowed_to_sell": str_list(a.get("reasonsNotAllowedToSell")),
        "about": clean(about.get("about")), "education": clean(about.get("education")),
        "events": clean(about.get("events")), "exhibitions": clean(about.get("exhibitions")),
        "website_url": clean(social.get("artistHomepage")), "facebook_url": clean(social.get("facebook")),
        "instagram_url": clean(social.get("instagram")), "pinterest_url": clean(social.get("pinterest")),
        "tiktok_url": clean(social.get("tiktok")), "twitter_url": clean(social.get("twitter")),
        "tumblr_url": clean(social.get("tumblr")),
        "meta_title": clean(meta.get("title")), "meta_description": clean(meta.get("description")),
    }
    out["badges"] = badges_of(a.get("badges"))
    out["collections"] = [
        {"is_featured": featured, "position": pos, "title": clean(col.get("title")),
         "collection_url": abs_url(col.get("collectionUrl")), "image_url": abs_url(col.get("artworkImage")),
         "artworks_count": to_int(col.get("artworksCount"))}
        for featured, key in ((False, "collections"), (True, "featuredCollections"))
        for pos, col in enumerate(a.get(key) or [], 1)]
    out["studio"] = [{"position": pos, "image_url": abs_url(img.get("thumbnailUrl")),
                      "description": clean(img.get("description"))}
                     for pos, img in enumerate(a.get("insideTheStudioImages") or [], 1)]

    for w in a.get("artworks") or []:
        wid = to_int(w.get("artworkID"))
        if not wid:
            continue
        pm = PDP_URL_RE.search(w.get("pdpUrl") or "")
        out["artworks"].append({
            "artwork_id": wid, "artist_id": aid, "title": clean(w.get("title")),
            "legacy_id": int(pm.group(3)) if pm else None, "slug": pm.group(1) if pm else None,
            "url": abs_url(w.get("pdpUrl")), "print_url": abs_url(w.get("printUrl")),
            "category": clean(w.get("category")), "subject": clean(w.get("subject")),
            "width_cm": num(w.get("widthInCentimeters")), "height_cm": num(w.get("heightInCentimeters")),
            "depth_cm": num(w.get("depthInCentimeters")), "aspect_ratio": num(w.get("aspectRatio")),
            "original_status": clean(w.get("originalStatus")),
            "has_open_editions": bool_or_none(w.get("hasOpenEditions")),
            "sku": clean(w.get("productSku")), "price_usd": usd(w.get("listPrice")),
            "min_print_price_usd": usd(w.get("printPrice")),
            "print_sizes": str_list(w.get("printDimensions")) or None,
            "print_materials": str_list(w.get("printMaterials")) or None,
            "image_url": abs_url(w.get("artworkImage")),
        })
        for kind, key in (("style", "styles"), ("medium", "mediums"), ("material", "materials")):
            _add_tags(out, wid, kind, w.get(key))


def _parse_artwork(out, data, meta, dl):
    out["page_type"] = "artwork"
    aw = data.get("artwork") or {}
    pdp = data.get("pdpArtwork") or {}
    ar = data.get("artist") or {}
    curator = data.get("curator") or {}
    wid = to_int(aw.get("artworkId") or pdp.get("artworkId"))
    aid = to_int(aw.get("userId") or ar.get("userId"))
    out["artwork_id"], out["artist_id"] = wid, aid

    profile_url = clean(ar.get("profileUrl"))
    username = profile_url.rstrip("/").rsplit("/", 1)[-1] if profile_url and "/account/" not in profile_url else None
    first, last = clean(ar.get("firstName")), clean(ar.get("lastName"))
    out["artist"] = {
        "artist_id": aid, "username": username, "first_name": first, "last_name": last,
        "full_name": clean(" ".join(x for x in (first, last) if x)), "profile_url": profile_url,
        "avatar_url": abs_url(clean(ar.get("avatarUrl"))), "studio_image_url": abs_url(clean(ar.get("studioImageUrl"))),
        "youtube_id": clean(ar.get("youtubeId")), "city": clean(ar.get("city")), "state": clean(ar.get("state")),
        "zipcode": clean(ar.get("postalCode")), "country": clean(ar.get("countryName")),
        "country_code": clean(ar.get("countryCode")), "joined_at": ts(ar.get("joinedAt")),
        "is_on_vacation": bool_or_none(ar.get("isArtistOnVacation")),
        "is_banned": bool_or_none(ar.get("isArtistBanned")),
        "about": clean(ar.get("about")), "description": clean(ar.get("description")),
    }
    out["badges"] = badges_of(ar.get("badges"))

    products = aw.get("products") or pdp.get("products") or []
    orig = next((p for p in products if p.get("isOriginal") or "original" in p), None)
    o = (orig or {}).get("original") or {}
    img = aw.get("artworkImage") or {}
    main = pdp.get("artworkMainImage") or {}
    dims = pdp.get("dimensions") or {}
    offer = ((meta.get("schema") or {}).get("product") or {}).get("offers") or {}
    ret = offer.get("hasMerchantReturnPolicy") or {}
    ship = o.get("shippingDimensions") or {}
    legacy = to_int(aw.get("legacyUserArtId") or pdp.get("legacyArtworkId"))
    out["artworks"].append({
        "artwork_id": wid, "legacy_id": legacy, "artist_id": aid,
        "title": clean(aw.get("title")), "slug": clean(aw.get("slug")),
        "url": clean(pdp.get("artworkOriginalUrl")) or meta.get("canonical"),
        "print_url": f"{BASE}/print/{aw.get('slug')}/{aid}/{legacy}/view" if aw.get("hasOpenEditions") else None,
        "category": clean(aw.get("category")), "subject": clean(aw.get("subject")),
        "description": clean(aw.get("description")), "year_produced": to_int(aw.get("yearProduced")),
        "width_cm": num(dims.get("widthInCentimeters")), "height_cm": num(dims.get("heightInCentimeters")),
        "depth_cm": num(dims.get("depthInCentimeters")), "size_bin": clean(pdp.get("artworkSizeBin")),
        "panels": to_int(aw.get("panels")), "is_multi_panel": bool_or_none(aw.get("isMultiPanel")),
        "aspect_ratio": num(img.get("aspectRatio")),
        "original_status": clean(pdp.get("originalArtworkStatus")),
        "has_original": bool_or_none(aw.get("hasOriginal")),
        "has_open_editions": bool_or_none(aw.get("hasOpenEditions")),
        "has_limited_editions": bool_or_none(aw.get("hasLimitedEditions")),
        "has_prints": bool(pdp.get("hasPrintsAvailable")),
        "sku": clean((orig or {}).get("sku")) or clean(pdp.get("sku")),
        "legacy_sku": clean((orig or {}).get("legacySku")),
        "price_usd": usd(o.get("listPrice")), "freight_usd": usd(o.get("freightAmount")),
        "min_print_price_usd": usd(pdp.get("minPrintPriceInCents")),
        "is_available_for_sale": bool_or_none((orig or {}).get("isAvailableForSale")),
        "is_sold_out": bool_or_none((orig or {}).get("isSoldOut")),
        "is_reserved": bool_or_none((orig or {}).get("isReserved")),
        "is_final_sale": bool_or_none((orig or {}).get("isFinalSale")),
        "is_framed": bool(o.get("hasFrame") or pdp.get("isFramed")), "frame_color": clean(o.get("frameColor")),
        "is_ready_to_hang": bool(o.get("isReadyToHang") or pdp.get("isReadyToHang")),
        "packaging_option": clean(o.get("packagingOption") or pdp.get("packagingOption")),
        "ships_from_country": clean(o.get("shippingCountry")),
        "ship_width": num(ship.get("width")), "ship_height": num(ship.get("height")),
        "ship_depth": num(ship.get("depth")), "ship_weight": num(ship.get("weight")),
        "views": to_int(aw.get("views")), "likes": to_int(aw.get("likes")) or 0,
        "visibility": clean(aw.get("visibility")), "curation_status": to_int(data.get("curationStatus")),
        "curator_name": clean(curator.get("name")), "curator_title": clean(curator.get("title")),
        "is_find_similar_available": bool_or_none(data.get("isFindSimilarAvailable")),
        "uploaded_at": ts(aw.get("uploadedAt")),
        "image_url": clean(img.get("imageUrl")), "image_base_url": clean(img.get("imageBaseUrl")),
        "image_fullscreen_url": clean(img.get("fullscreenUrl")), "image_thumbnail_url": clean(img.get("thumbnailUrl")),
        "print_image_url": clean(main.get("printMainUrl")),
        "image_width": to_int(img.get("width")), "image_height": to_int(img.get("height")),
        "meta_title": clean(meta.get("title")), "meta_description": clean(meta.get("description")),
        "return_days": to_int(ret.get("merchantReturnDays")),
        "has_detail": True,
    })
    for kind, key in (("style", "styles"), ("medium", "mediums"), ("material", "materials"), ("keyword", "keywords")):
        _add_tags(out, wid, kind, aw.get(key) or pdp.get(key))

    out["images"].append({"artwork_id": wid, "position": 0, "kind": "main", "image_url": clean(img.get("imageUrl")),
                          "base_url": clean(img.get("imageBaseUrl")), "fullscreen_url": clean(img.get("fullscreenUrl")),
                          "thumbnail_url": clean(img.get("thumbnailUrl")), "width": to_int(img.get("width")),
                          "height": to_int(img.get("height")), "aspect_ratio": num(img.get("aspectRatio")),
                          "caption": clean(img.get("description"))})
    for pos, ai in enumerate(aw.get("additionalImages") or [], 1):
        out["images"].append({"artwork_id": wid, "position": pos, "kind": "additional",
                              "image_url": clean(ai.get("imageUrl")), "base_url": clean(ai.get("imageBaseUrl")),
                              "fullscreen_url": None, "thumbnail_url": clean(ai.get("thumbnailUrl")),
                              "width": to_int(ai.get("width")), "height": to_int(ai.get("height")),
                              "aspect_ratio": num(ai.get("aspectRatio")), "caption": clean(ai.get("description"))})

    for p in products:
        sku = clean(p.get("sku"))
        if not sku:
            continue
        po, pp = p.get("original") or {}, p.get("print") or {}
        rates = pp.get("shippingRates") or {}
        ptype = ("original" if p.get("isOriginal") else "open_edition" if p.get("isOpenEdition")
                 else "limited_edition" if p.get("isLimitedEdition") else "print")
        out["products"].append({
            "sku": sku, "artwork_id": wid, "product_type_id": to_int(p.get("id")), "product_type": ptype,
            "legacy_sku": clean(p.get("legacySku")), "material": clean(p.get("material")),
            "width": num(p.get("width")), "height": num(p.get("height")), "depth": num(p.get("depth")),
            "units_produced": to_int(p.get("unitsProduced")),
            "is_available_for_sale": bool_or_none(p.get("isAvailableForSale")),
            "is_sold_out": bool_or_none(p.get("isSoldOut")), "is_reserved": bool_or_none(p.get("isReserved")),
            "is_final_sale": bool_or_none(p.get("isFinalSale")), "is_aple": bool_or_none(p.get("isAple")),
            "price_usd": usd(po.get("listPrice") or pp.get("listPrice")),
            "freight_usd": usd(po.get("freightAmount")),
            "shipping_domestic_usd": usd(rates.get("domestic")), "shipping_intl_usd": usd(rates.get("international")),
            "recommended_option_id": clean(str(pp["recommendedOptionId"])) if pp.get("recommendedOptionId") else None,
        })
        for pos, opt in enumerate(p.get("options") or [], 1):
            if opt.get("id") is None:
                continue
            out["options"].append({
                "sku": sku, "option_id": str(opt["id"]), "position": pos, "title": clean(opt.get("title")),
                "description": clean(opt.get("description")),
                "extended_description": clean(opt.get("extendedDescription")),
                "framing_type_id": to_int(opt.get("framingTypeId")), "price_usd": usd(opt.get("price")),
                "width": num(opt.get("width")), "height": num(opt.get("height"))})

    prices = o.get("listPrices") or pdp.get("geoPricesInCents") or {}
    freights = o.get("freightAmounts") or {}
    for region in sorted(set(prices) | set(freights)):
        out["region_prices"].append({"artwork_id": wid, "region_code": region,
                                     "price_usd": usd(prices.get(region)), "freight_usd": usd(freights.get(region))})
