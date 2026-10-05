"""Parse one Artmajeur HTML page into rows per table.

Two page types: artwork pages (/<artist>/en/artworks/<id>/<slug>) and artist profiles (/<artist>).
Data comes from JSON-LD, the analytics dataLayer object and the rendered HTML.

parse_page() returns:
    rows:    {table: [row, ...]}           rows to upsert
    replace: {table: (column, [values])}   child rows to delete first (re-inserted from `rows`)
"""
import html as htmllib
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

BASE = "https://www.artmajeur.com"
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
DATALAYER_RE = re.compile(r"window\.dataLayer\.push\(\s*(\{.*?\})\s*\);", re.S)
ARTWORK_URL_RE = re.compile(r"/([^/]+)/[a-z]{2}/artworks/(\d+)/([^/?#]*)")
MONEY_RE = re.compile(r"[\d][\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
DIM_RE = re.compile(r"(Height|Width|Depth)\s*([\d.,]+)\s*(in|cm)", re.I)
ORG_NAMES = {"ARTMAJEUR"}


def clean(v):
    if not isinstance(v, str):
        return v
    v = re.sub(r"\s+", " ", htmllib.unescape(v).replace("​", "")).strip()
    return v or None


def txt(el, sep=" "):
    return clean(el.get_text(sep, strip=True)) if el else None


def money(s):
    m = MONEY_RE.search(s or "")
    return float(m.group().replace(",", "")) if m else None


def to_int(s):
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group().replace(",", "")) if m else None


def year_of(s):
    m = YEAR_RE.search(s or "")
    return int(m.group(1)) if m else None


def abs_url(u):
    if not u:
        return None
    return BASE + u if u.startswith("/") else u


def parse_date(s, fmts=("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d")):
    s = clean(s)
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except (TypeError, ValueError):
            pass
    return None


def conv(dim, unit):
    """dim = (value, 'in'|'cm') -> value in the requested unit."""
    if not dim:
        return None
    v, u = dim
    if u == unit:
        return v
    return round(v * 2.54, 1) if unit == "cm" else round(v / 2.54, 1)


def add(out, table, row):
    out["rows"].setdefault(table, []).append(row)


def person_fields(p):
    """Location/job/bio from a JSON-LD Person (street address intentionally dropped)."""
    addr = p.get("address") or {}
    gender = (p.get("gender") or "").rsplit("/", 1)[-1] or None
    return {"name": clean(p.get("name")), "job_title": clean(p.get("jobTitle")), "gender": gender,
            "city": clean(addr.get("addressLocality")), "region": clean(addr.get("addressRegion")),
            "postal_code": clean(addr.get("postalCode")), "country_code": clean(addr.get("addressCountry")),
            "social_links": [u for u in p.get("sameAs") or [] if u] or None}


def artist_info_list(soup):
    """The 'Nationality / Date of birth / Artistic domains / Groups' list in the biography block."""
    info, groups = {}, []
    for li in soup.select("li"):
        st = li.find("strong")
        if not st:
            continue
        label = (txt(st) or "").rstrip(" :")
        if label not in ("Nationality", "Date of birth", "Artistic domains", "Groups"):
            continue
        st.extract()
        if label == "Groups":
            groups = [(txt(a), abs_url(a.get("href"))) for a in li.find_all("a")]
        elif label == "Artistic domains":
            info[label] = [txt(a) for a in li.find_all("a")] or [x for x in (txt(li) or "").split(", ") if x]
        else:
            info[label] = txt(li)
    return info, groups


def parse_page(html):
    out = {"page_type": "unknown", "rows": {}, "replace": {}}
    lds = []
    for m in LD_RE.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        lds += d if isinstance(d, list) else [d]
    dl = {}
    m = DATALAYER_RE.search(html)
    if m:
        try:
            dl = {k: v for k, v in json.loads(m.group(1)).items()
                  if v not in ("", None, []) and k not in ("artmajeur_session_id", "user_id", "cart_amount")}
        except json.JSONDecodeError:
            pass
    out["raw"] = {"jsonld": [x for x in lds if x.get("@type") not in ("WebSite", "Organization")
                             and x.get("name") not in ORG_NAMES], "dataLayer": dl}

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "svg", "noscript"]):
        t.decompose()
    c = soup.find("link", rel="canonical")
    out["canonical_url"] = c.get("href") if c else None
    meta = {}
    for mt in soup.find_all("meta"):
        k = mt.get("name") or mt.get("property")
        if k and mt.get("content") is not None:
            meta.setdefault(k, mt["content"])
    out["meta"] = meta

    if dl.get("route") == "profile_artwork" or soup.find(id="artwork_pane"):
        _artwork_page(out, soup, lds, dl, meta)
    elif dl.get("route") == "profile_index" or any(x.get("@type") == "ProfilePage" for x in lds):
        _artist_page(out, soup, lds, dl, meta)
    return out


# ---- artwork cards (profile grid, "See more from ...") -------------------------------
def artwork_cards(out, soup, artist_id):
    seen = set()
    for card in soup.select("[data-hit-target-url]"):
        m = ARTWORK_URL_RE.search(card.get("data-hit-target-url") or "")
        if not m or card.find_parent(id="artwork_pane") is not None:
            continue
        wid = int(m.group(2))
        if wid in seen or card.find("img") is None:
            continue
        seen.add(wid)
        img = card.find("img")
        alt = img.get("alt") or ""
        am = re.match(r'(.+?) titled "(.*)" by (.+?)(?:, (.*))?$', alt)
        title_el = card.select_one(".text-truncate")
        spans = [txt(s) for s in card.select(".text-secondary span, .fw-light span")]
        medium_line = next((s for s in spans if s and not s.startswith("|")), None)
        size = next((s.lstrip("| ") for s in spans if s and s.startswith("|")), None)
        price_el = card.select_one("span.fw-bold")
        prints = card.find("a", href=re.compile(r"#print$"))
        lic = card.find("a", href=re.compile(r"#licence$"))
        sold = any((txt(x) or "") == "Sold" for x in card.select(".text-danger"))
        tech = medium_line.split(" on ", 1) if medium_line else [None]
        add(out, "artworks", {
            "artwork_id": wid, "artist_id": artist_id, "slug": m.group(3) or None,
            "url": abs_url(card["data-hit-target-url"]),
            "title": (am.group(2) if am else None) or txt(title_el),
            "medium": am.group(1) if am else None, "technique": clean(tech[0]),
            "support": clean(tech[1]) if len(tech) > 1 else None, "dimensions": size,
            "price_usd": money(txt(price_el)) if price_el else None,
            "availability": "sold" if sold else ("for_sale" if price_el else None),
            "has_prints": True if prints else None,
            "print_price_from_usd": money(txt(prints)) if prints else None,
            "has_licences": True if lic else None,
            "licence_price_from_usd": money(txt(lic)) if lic else None,
            "image_url": img.get("src") or img.get("data-src"),
        })


# ---- artwork page ------------------------------------------------------------------
def _artwork_page(out, soup, lds, dl, meta):
    out["page_type"] = "artwork"
    url = out["canonical_url"] or meta.get("og:url")
    um = ARTWORK_URL_RE.search(url or "")
    wid = to_int(str(dl.get("artwork_id") or dl.get("profile_artwork_id") or "")) or (int(um.group(2)) if um else None)
    aid = to_int(str(dl.get("account_artmajeurId") or dl.get("profile_artist_id") or ""))
    out["artwork_id"], out["artist_id"] = wid, aid
    product = next((x for x in lds if x.get("@type") == "Product" and x.get("name") not in ORG_NAMES
                    and x.get("productId")), {})
    offer = product.get("offers") or {}
    creator = product.get("creator") or {}
    images = product.get("image") or []
    image = images[0] if images else {}
    ret = offer.get("hasMerchantReturnPolicy") or {}

    # artist (as shown on the artwork page)
    info, groups = artist_info_list(soup.select_one("#collapsed_biography_container") or soup)
    portrait = soup.find("img", alt=re.compile(r" Portrait$"))
    bio = soup.select_one("#collapsed_biography_container #full_description_text")
    if bio:
        for ul in bio.find_all("ul"):
            ul.decompose()
    slug = um.group(1) if um else None
    h1_link = soup.select_one("h1 a[href]")
    person = person_fields(creator)
    if not person["name"] and h1_link:
        person["name"] = txt(h1_link)
    add(out, "artists", dict(person, artist_id=aid, slug=slug, url=f"{BASE}/{slug}" if slug else None,
                             categories=clean(dl.get("profile_artist_category")),
                             biography=txt(bio) or clean(creator.get("description")),
                             nationality=clean(info.get("Nationality")), birth_date=clean(info.get("Date of birth")),
                             birth_year=year_of(info.get("Date of birth")),
                             artistic_domains=info.get("Artistic domains") or None,
                             portrait_url=portrait.get("src") if portrait else None))
    out["replace"]["artist_groups"] = ("artist_id", [aid])
    for pos, (name, gurl) in enumerate(groups, 1):
        add(out, "artist_groups", {"artist_id": aid, "position": pos, "kind": "group", "name": name, "url": gurl})

    # title block
    h1 = soup.find("h1")
    h1_spans = h1.find_all("span") if h1 else []
    year = next((year_of(txt(s)) for s in h1_spans if re.fullmatch(r"\(.*\)", txt(s) or "")), None)

    # details list
    details, categories = {}, []
    edition = medium = technique = support = edition_size = None
    ul = next((u for u in soup.select("ul.list-unstyled") if "Dimensions" in u.get_text()), None)
    for li in (ul.find_all("li", recursive=False) if ul else []):
        for modal in li.select(".modal"):
            modal.decompose()
        st = li.find("strong")
        if not st:
            continue
        label = txt(st)
        st.extract()
        if label == "Categories":
            categories = [(txt(a), abs_url(a.get("href"))) for a in li.find_all("a")]
            details[label] = [c[0] for c in categories]
            continue
        value = txt(li)
        if medium is None and re.search(r"Edition|Original|Artwork", label or "") and "," in (value or ""):
            edition = label                           # e.g. "Painting, Oil on Canvas"
            em = re.match(r"Number of copies\s*:\s*(\d+)\.\s*(.*)", value)
            if em:                                    # limited editions: "Number of copies : 20. Photography, ..."
                edition_size, value = int(em.group(1)), em.group(2)
            medium, rest = [clean(x) for x in value.split(",", 1)]
            technique, _, support = (rest or "").partition(" on ")
            technique, support = clean(technique), clean(support)
        details[label] = value
    dims = {k.lower(): (float(v.replace(",", "")), u.lower()) for k, v, u in DIM_RE.findall(details.get("Dimensions") or "")}

    # badges row
    badges, tooltips = [], {}
    for tile in soup.select(".row.text-center.small > div"):
        label = txt(tile)
        if not label:
            continue
        badges.append(label)
        tip = tile.find(attrs={"title": True})
        tooltips[label] = clean(tip["title"]) if tip else None
    favorites = next((to_int(b) for b in badges if "collection" in b), None)
    mounted = next((b[len("Mounted on "):] for b in badges if b.startswith("Mounted on ")), None)
    signed = next((b for b in badges if "signed" in b.lower()), None)

    # purchase panes
    pane = soup.find(id="productTypesContent")
    orig = pane.find(id="original") if pane else None
    tab = soup.select_one("#productTypes #original-tab")
    orig_text = txt(orig) or ""
    price_el = orig.select_one("[data-analytics-price]") if orig else None
    availability = ("sold" if re.search(r"\bSold\b", orig_text[:200]) else
                    "not_for_sale" if "Not For Sale" in orig_text else
                    "price_on_request" if "Ask the price" in orig_text else "for_sale" if price_el else
                    "reproductions_only" if orig is None and pane is not None else None)
    ship_from = orig.find(string=re.compile("Shipping from")) if orig else None
    ship_country = txt(ship_from.find_next("strong")) if ship_from else None
    within = re.search(r"Ships within (\d+) days", orig_text)
    pack = re.search(r"\(([^)]*packaging[^)]*)\)", orig_text, re.I)

    def options(sel):
        return [txt(o) for o in soup.select(sel + " option") if txt(o)] or None
    prints = pane.find(id="print") if pane else None
    lic = pane.find(id="licence") if pane else None

    # description and keywords
    desc = soup.select_one("#collapsed_artwork_about_container #full_description_text")
    title = clean(product.get("name")) or (txt(h1_spans[0]) if h1_spans else None)
    keywords = None
    for fc in soup.find_all("figcaption"):
        t = txt(fc) or ""
        if title and t.startswith(title) and " - " in t:
            keywords = [k.strip() for k in t.rsplit(" - ", 1)[-1].split(",") if k.strip()]
            break
    img_hd = meta.get("og:image")

    add(out, "artworks", {
        "artwork_id": wid, "artist_id": aid, "title": title, "slug": um.group(3) if um else None, "url": url,
        "year": year,
        "category": clean(dl.get("profile_artwork_category")) or (re.sub(r"\W+", "_", medium.lower()) if medium else None),
        "medium": medium or clean(product.get("category")), "technique": technique, "support": support,
        "sub_categories": clean(dl.get("profile_artwork_sub_categories")),
        "style": clean(dl.get("profile_artwork_style")), "theme": clean(dl.get("profile_artwork_theme")),
        "edition_type": edition, "edition_size": edition_size, "copies_available": details.get("Number of copies available"),
        "dimensions": details.get("Dimensions"),
        **{f"{d}_{unit}": conv(dims.get(d), unit) for d in ("height", "width", "depth") for unit in ("in", "cm")},
        "condition": details.get("Artwork's condition"), "framing": details.get("Framing"),
        "is_framed": ("This artwork is framed" in badges) or bool(re.search(r"\bis framed\b", details.get("Framing") or "")),
        "fit_for_outdoor": ("Fit for outdoor" in badges) or (details.get("Fit for outdoor?") or "").lower().startswith("yes"),
        "is_ai_generated": "AI generated image" in details,
        "is_one_of_a_kind": "One of a kind" in badges, "is_signed": bool(signed),
        "signature_details": tooltips.get(signed) if signed else None,
        "has_certificate": any("Certificate" in b for b in badges),
        "is_ready_to_hang": "Ready to hang" in badges, "mounted_on": mounted, "favorites_count": favorites,
        "availability": availability, "sale_type": txt(tab),
        "price_usd": money(price_el.get("data-analytics-price")) if price_el else None,
        "offer_price_usd": money(str(offer.get("price"))) if offer.get("price") is not None else None,
        "shipping_included": True if "Shipping included" in orig_text else False if "Shipping Not Included" in orig_text else None,
        "ships_from_country": ship_country, "packaging": clean(pack.group(1)) if pack else None,
        "ships_within_days": int(within.group(1)) if within else None,
        "return_days": ret.get("merchantReturnDays"), "last_copies": "Last copies remaining" in orig_text,
        "has_prints": prints is not None,
        "print_price_from_usd": money(txt(prints.select_one("#print_price_label"))) if prints else None,
        "print_types": options("#print_type"), "print_sizes": options("#print_productId"),
        "print_framings": options("#print_option"), "print_finishes": options("#print_aspect"),
        "has_licences": lic is not None,
        "licence_price_from_usd": money(txt(lic.select_one("#licence_price_label"))) if lic else None,
        "licence_types": options("#licence_type"),
        "description": txt(desc) or clean(product.get("description")), "keywords": keywords,
        "badges": badges or None, "image_url": image.get("contentUrl"), "image_hd_url": img_hd,
        "published_on": image.get("datePublished"), "meta_description": clean(meta.get("description")),
        "details": details or None, "has_detail": True,
    })
    out["replace"]["artwork_categories"] = ("artwork_id", [wid])
    for pos, (name, curl) in enumerate(categories, 1):
        add(out, "artwork_categories", {"artwork_id": wid, "position": pos, "name": name, "url": curl})

    more = soup.find(string=re.compile(r"See more from"))
    if more:
        artwork_cards(out, more.find_parent("div", id=True) or soup, aid)


# ---- artist profile page -----------------------------------------------------------
def _artist_page(out, soup, lds, dl, meta):
    out["page_type"] = "artist"
    prof = next((x for x in lds if x.get("@type") == "ProfilePage"), {})
    person = prof.get("mainEntity") or {}
    aid = to_int(str(dl.get("account_artmajeurId") or dl.get("profile_artist_id") or ""))
    out["artist_id"] = aid
    url = out["canonical_url"]
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else None
    pres = soup.find(id="presentation") or soup
    info, groups = artist_info_list(pres)
    bio_h = pres.find("h3", string=re.compile("Biography"))
    bio = bio_h.parent if bio_h else None
    bio_text = None
    if bio:
        parts = [txt(p) for p in bio.find_all("p") if txt(p)]
        bio_text = "\n\n".join(parts) or None
    reviews = [x for x in lds if x.get("@type") == "Review"]
    agg = next((((r.get("itemReviewed") or {}).get("aggregateRating")) for r in reviews
                if (r.get("itemReviewed") or {}).get("aggregateRating")), None) or {}

    activity = {}
    act_h = soup.find("h3", string=re.compile("Activity on ArtMajeur"))
    if act_h:
        for div in act_h.parent.select("div.mt-1 > div"):
            t = txt(div) or ""
            for key, pat in (("member_since", r"Member since\s*(\d{4})"), ("last_mod", r"Last modification date\s*:\s*([A-Z][a-z]{2} \d+, \d{4})"),
                             ("views", r"Image views:\s*([\d,]+)"), ("fav_by_others", r"added to favorite collections:\s*([\d,]+)"),
                             ("followers", r"Followers:\s*([\d,]+)"), ("reviews", r"Reviews and comments:\s*([\d,]+)"),
                             ("followed", r"Followed artists:\s*([\d,]+)"), ("my_favs", r"My favorite artworks:\s*([\d,]+)")):
                m = re.search(pat, t)
                if m:
                    activity[key] = m.group(1)

    cert = {}
    cert_sec = soup.find(id="certification")
    if cert_sec and "No data available" not in cert_sec.get_text():
        line = txt(cert_sec.select_one("p.font-size-24")) or ""
        m = re.search(r"Artist Value\s+(.*?)\s+(\d{4})\s*\|\s*(\S)([\d,.]+)\s*\(\$([\d,.]+)\)", line)
        if m:
            cur = {"€": "EUR", "$": "USD", "£": "GBP"}.get(m.group(3), m.group(3))
            cert = {"certified_value_category": clean(m.group(1)), "certified_value_year": int(m.group(2)),
                    "certified_value_currency": cur, "certified_value": money(m.group(4)),
                    "certified_value_usd": money(m.group(5))}
        by = re.search(r"established by (.+?) on ([A-Z][a-z]{2} \d+, \d{4})", cert_sec.get_text(" "))
        if by:
            cert.update(certified_by=clean(by.group(1)), certified_on=parse_date(by.group(2)))

    portrait = soup.find("img", alt=re.compile(r" Portrait$"))
    add(out, "artists", dict(
        person_fields(person), artist_id=aid, slug=slug, url=url,
        categories=clean(dl.get("profile_artist_category")),
        biography=bio_text or clean(re.sub(r"<[^>]+>", " ", person.get("description") or "")),
        nationality=clean(info.get("Nationality")), birth_date=clean(info.get("Date of birth")),
        birth_year=year_of(info.get("Date of birth")), artistic_domains=info.get("Artistic domains") or None,
        portrait_url=portrait.get("src") if portrait else None,
        rating_value=agg.get("ratingValue"), rating_count=agg.get("ratingCount"),
        followers_count=to_int(activity.get("followers")), followed_artists_count=to_int(activity.get("followed")),
        favorited_artworks_count=to_int(activity.get("fav_by_others")),
        favorite_artworks_count=to_int(activity.get("my_favs")), image_views=to_int(activity.get("views")),
        reviews_count=to_int(activity.get("reviews")), member_since=to_int(activity.get("member_since")),
        last_modified_at=parse_date(activity.get("last_mod")),
        profile_created_at=prof.get("dateCreated"), profile_modified_at=prof.get("dateModified"),
        meta_title=clean(soup.title.string) if soup.title else None, meta_description=clean(meta.get("description")),
        has_page=True, **cert))

    children = ["artist_recognitions", "artist_groups", "artist_influences", "artist_education",
                "artist_achievements", "artist_events", "artist_news", "artist_reviews"]
    out["replace"] = {t: ("artist_id", [aid]) for t in children}

    for pos, r in enumerate(soup.select("#recognition .recognition"), 1):
        add(out, "artist_recognitions", {"artist_id": aid, "position": pos, "name": txt(r.select_one(".fw-bolder")),
                                         "description": txt(r.select_one(".text-secondary"))})
    pos = 0
    for name, gurl in groups:
        pos += 1
        add(out, "artist_groups", {"artist_id": aid, "position": pos, "kind": "group", "name": name, "url": gurl})
    gal_h = soup.find(["h2", "h3"], string=re.compile("Galleries"))
    if gal_h:
        for a in gal_h.parent.find_all("a", href=True):
            name = txt(a)
            if not name:
                continue
            pos += 1
            nxt = a.next_sibling
            det = re.search(r"\(([^)]*)\)", str(nxt) if nxt else "")
            add(out, "artist_groups", {"artist_id": aid, "position": pos, "kind": "gallery", "name": name,
                                       "details": det.group(1) if det else None, "url": abs_url(a["href"])})

    infl_h = soup.find("h3", string=re.compile("Influences"))
    if infl_h and "No data available" not in infl_h.parent.get_text():
        names = [txt(a) for a in infl_h.parent.find_all(["a", "span"]) if txt(a) and txt(a) != ","]
        if not names:
            names = [n.strip() for n in (txt(infl_h.parent) or "").replace("Influences", "", 1).split(",")]
        for i, n in enumerate(dict.fromkeys(x for x in names if x), 1):
            add(out, "artist_influences", {"artist_id": aid, "position": i, "name": n})

    for div in soup.select("#educationsContainer > div[id^=education_]"):
        years = txt(div.select_one(".fw-bold"))
        ys = [int(y) for y in re.findall(r"\d{4}", years or "")]
        add(out, "artist_education", {"artist_id": aid, "education_id": to_int(div["id"]), "years": years,
                                      "start_year": ys[0] if ys else None, "end_year": ys[-1] if ys else None,
                                      "description": clean((txt(div) or "").replace(years or "", "", 1))})

    for h4 in soup.select("#achievements h4"):
        kind = h4.get("id") or txt(h4)
        box = h4.parent
        for div in box.select("div[id^=achievement_]"):
            yr = txt(div.select_one(".fw-bold"))
            a = div.find("a", href=True)
            add(out, "artist_achievements", {"artist_id": aid, "achievement_id": to_int(div["id"]), "kind": kind,
                                             "year": year_of(yr),
                                             "description": clean((txt(div) or "").replace(yr or "", "", 1)),
                                             "file_url": a["href"] if a else None})

    ev_h = soup.find("h3", string=re.compile("Ongoing and Upcoming"))
    if ev_h and "No data available" not in ev_h.parent.get_text():
        items = [c for c in ev_h.parent.find_all(recursive=False) if c.name != "h3"]
        for i, item in enumerate(items, 1):
            a = item.find("a", href=True)
            title = txt(item.find(["h4", "h5", "strong"])) or (txt(a) if a else None)
            add(out, "artist_events", {"artist_id": aid, "position": i, "title": title, "details": txt(item, " | "),
                                       "url": abs_url(a["href"]) if a else None})

    news = soup.find(id="profile_news")
    for i, more in enumerate(news.find_all("a", href=re.compile(r"/news/\d+")) if news else [], 1):
        card = more
        while card.parent is not None and card.parent is not news and not card.find("h4"):
            card = card.parent
        ext = card.find("a", href=re.compile(r"^https?://(?!www\.artmajeur)"))
        added = card.find(string=re.compile(r"^\s*Added "))
        nm = re.search(r"/news/(\d+)", more["href"])
        body = "\n\n".join(t for t in (txt(p) for p in card.find_all("p")) if t)
        add(out, "artist_news", {"artist_id": aid, "position": i, "news_id": int(nm.group(1)) if nm else None,
                                 "title": txt(card.find("h4")),
                                 "added_on": parse_date(re.sub(r"^\s*Added ", "", added)) if added else None,
                                 "url": abs_url(more["href"]), "external_url": ext["href"] if ext else None,
                                 "body": body or None})

    for i, r in enumerate(reviews, 1):
        author = r.get("author") or {}
        add(out, "artist_reviews", {"artist_id": aid, "position": i,
                                    "rating": (r.get("reviewRating") or {}).get("ratingValue"),
                                    "body": clean(r.get("reviewBody")),
                                    "published_on": (r.get("comment") or {}).get("datePublished"),
                                    "author_name": clean(author.get("name")), "author_url": author.get("url"),
                                    "author_country": (author.get("address") or {}).get("addressCountry")})

    artwork_cards(out, soup.find(id="profileArtworks") or soup, aid)
