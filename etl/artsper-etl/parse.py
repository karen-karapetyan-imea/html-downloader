"""Parse one Artsper artist page (raw HTML) into a structured dict."""
import base64
import html as htmllib
import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE = "https://www.artsper.com"
ARTIST_URL_RE = re.compile(r"/contemporary-artists/([^/]+)/(\d+)/([^/?#]+)")
ARTWORK_URL_RE = re.compile(r"/contemporary-artworks/([^/]+)/(\d+)/")
EXHIBITION_URL_RE = re.compile(r"/contemporary-art-galleries-exhibitions/(\d+)/")


def clean(text):
    if text is None:
        return None
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


def txt(el, sep=" "):
    return clean(el.get_text(sep, strip=True)) if el else None


def abs_url(u):
    return urljoin(BASE, u) if u else None


def deobfuscate(u):
    # Artsper hides some links in data-infos with "/" replaced by "stcdsq"
    return abs_url(u.replace("stcdsq", "/")) if u else None


def gtm(el):
    raw = el.get("data-gtm-analytics") if el else None
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw))
    except Exception:
        return None


def to_int(s):
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*", str(s))
    return int(m.group().replace(",", "")) if m else None


def to_num(s):
    if s in (None, ""):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def parse_money(s):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group().replace(",", "")) if m else None


def parse_dims(s):
    """'Painting . 85 x 85 x 4 cm' -> ('Painting', '85 x 85 x 4 cm', [85, 85, 4])"""
    s = clean(s) or ""
    label, _, dims = s.rpartition(" . ") if " . " in s else ("", "", s)
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", dims)]
    nums += [None] * (3 - len(nums))
    return clean(label), clean(dims), nums[:3]


def parse_date(s):
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_exhibition_dates(text):
    text = clean(text) or ""
    m = re.search(r"From\s+(.+?)\s+to\s+(.+)$", text, re.S)
    if m:
        return parse_date(m.group(1)), parse_date(m.group(2))
    m = re.search(r"(?:Since|From|On)\s+(.+)$", text)
    if m:
        return parse_date(m.group(1)), None
    return None, None


def artist_id_from_url(u):
    m = ARTIST_URL_RE.search(u or "")
    return int(m.group(2)) if m else None


def parse_page(html, url=None):
    soup = BeautifulSoup(html, "lxml")
    out = {"page_type": "unknown"}

    jsonld = []
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            jsonld.append(json.loads(sc.string or ""))
        except Exception:
            jsonld.append({"_unparsed": sc.string})
    ld = {d.get("@type"): d for d in jsonld if isinstance(d, dict)}
    meta_tags = [dict(m.attrs) for m in soup.find_all("meta") if m.attrs]
    meta = {}
    for m in meta_tags:
        key = m.get("name") or m.get("property") or m.get("itemprop")
        if key and "content" in m:
            meta.setdefault(key, m["content"])

    ctx = soup.select_one("#page-context")
    person = ld.get("Person")
    if not ctx or not person or not ctx.get("data-artist-id"):
        if soup.select_one("#catalog-artists"):
            out["page_type"] = "artist_listing"
        return out
    out["page_type"] = "artist"
    for t in soup(["script", "style", "svg", "noscript"]):
        t.decompose()
    main = soup.find("main") or soup

    artist_id = int(ctx["data-artist-id"])
    a_url = person.get("url") or url
    m = ARTIST_URL_RE.search(a_url or "")

    # ---- heading line: nationality, years, followers, selections
    birth_year = to_int(person.get("birthDate"))
    death_year = to_int(person.get("deathDate"))
    followers = None
    selections = []
    for item in main.select(".heading__second-line .selection__item"):
        label = txt(item)
        if label:
            selections.append(label)
    for item in main.select(".heading__second-line .heading__item"):
        if item.find("i", class_="selection__item__icon"):
            continue
        t = txt(item, " | ") or ""
        f = re.search(r"([\d,]+)\s+followers?", t)
        if f:
            followers = to_int(f.group(1))
        years = re.findall(r"\b(1\d{3}|20\d{2})\b", re.sub(r"[\d,]+\s+followers?", "", t))
        if years and birth_year is None and not re.search(r"[-–]\s*" + years[0], t):
            birth_year = int(years[0])
        if len(years) > 1 and death_year is None:
            death_year = int(years[-1])
        elif len(years) == 1 and re.search(r"[-–]\s*" + years[0], t) and death_year is None:
            death_year = int(years[0])
    selections = list(dict.fromkeys(selections))

    # ---- biography / quote
    bio_el = main.select_one(".section-biography__biography")
    bio_html = bio_text = None
    if bio_el:
        for more in bio_el.select(".block-more"):
            more.decompose()
        body = bio_el.find("div") or bio_el
        bio_html = body.decode_contents().strip() or None
        paras = [txt(p) for p in body.find_all("p")] or [txt(body)]
        bio_text = clean("\n\n".join(p for p in paras if p))
    quote = txt(main.select_one(".section-quote__quote"))
    if quote:
        quote = quote.strip("«» \"“”")

    # ---- about block
    attributes = []
    nationality_label = nationality_url = None
    for item in main.select(".about__block__item"):
        title = txt(item.select_one(".about__block__item__title"))
        if not title:
            continue
        attr_type = re.sub(r"\W+", "_", title.lower()).strip("_")
        desc = item.select_one(".about__block__item__description")
        links = desc.find_all("a") if desc else []
        values = [(txt(a), abs_url(a.get("href"))) for a in links] or (
            [(v.strip(), None) for v in (txt(desc) or "").split(",") if v.strip()])
        for i, (v, u) in enumerate(values, 1):
            attributes.append({"attr_type": attr_type, "value": v, "url": u, "position": i})
        if attr_type == "nationality" and values:
            nationality_label, nationality_url = values[0]

    # ---- cover image / paginator / filters
    cover = main.select_one(".section-studio__image img")
    cover_url = cover.get("data-src") or cover.get("src") if cover else None
    pages_total = 1
    pag = main.select_one(".paginator .pagination-mobile")
    if pag and "/" in (txt(pag) or ""):
        pages_total = to_int(txt(pag).split("/")[-1])

    filters = []
    for i, chip in enumerate(main.select(".section-filter .chip"), 1):
        filters.append({"position": i, "label": txt(chip),
                        "url": deobfuscate(chip.get("data-infos")) or abs_url(chip.get("href")),
                        "is_active": "active" in chip.get("class", [])})

    # ---- artworks
    featured = {}
    for li in (ld.get("ItemList") or {}).get("itemListElement", []):
        it = li.get("item", {})
        aw = ARTWORK_URL_RE.search(it.get("url", ""))
        if aw:
            featured[int(aw.group(2))] = (li.get("position"), it.get("image"))

    artworks = []
    for pos, card in enumerate(main.select("article.card-artwork"), 1):
        g = gtm(card) or {}
        img = card.select_one("img.card-artwork__image") or card.find("img")
        awid = to_int(card.get("data-id")) or g.get("product_id")
        href = card.get("data-url") or (card.select_one("a") or {}).get("href")
        cat_m = ARTWORK_URL_RE.search(href or "")
        cm_label, cm_text, cm = parse_dims(txt(card.select_one(".measure--cm")))
        _, in_text, inch = parse_dims(txt(card.select_one(".measure--inch")))
        price_el = card.select_one(".card-artwork__price--newer") or card.select_one(".card-artwork__price")
        old_el = card.select_one(".card-artwork__price--older")
        price_text = txt(price_el)
        sold = bool(card.select_one(".card-artwork__price--soldout")) or (price_text or "").lower() == "sold"
        on_request = "request" in (price_text or "").lower()
        srcset = (img.get("data-srcset") or "") if img else ""
        img2x = re.search(r"(\S+)\s+2x", srcset)
        ar = re.search(r"aspect-ratio:\s*([\d.]+)", img.get("style", "")) if img else None
        staff = card.select_one(".card-artwork__staffpicks-label")
        f = featured.get(awid)
        artworks.append({
            "artwork_id": awid, "position": pos,
            "title": (card.select_one(".card-artwork__title") or {}).get("title") or txt(card.select_one(".card-artwork__title")) or g.get("name"),
            "url": abs_url(href),
            "category": g.get("artworkCategory") or (img.get("data-category") if img else None) or (cat_m.group(1) if cat_m else None),
            "medium_label": cm_label,
            "dimensions_cm": cm_text, "width_cm": cm[0], "height_cm": cm[1], "depth_cm": cm[2],
            "dimensions_in": in_text, "width_in": inch[0], "height_in": inch[1], "depth_in": inch[2],
            "price_text": price_text,
            "price_usd": None if sold or on_request else parse_money(price_text),
            "price_usd_old": parse_money(txt(old_el)) if old_el else None,
            "is_discounted": bool(old_el), "is_sold": sold, "is_price_on_request": on_request,
            "price_usd_data": to_num(img.get("data-price")) if img else None,
            "price_eur": to_num(g.get("price")), "value_eur": to_num(g.get("value")),
            "currency": g.get("currency"), "price_range": g.get("priceRange"),
            "quantity": g.get("quantity"),
            "vendor_id": g.get("vendor_id") or (to_int(img.get("data-vendor")) if img else None),
            "gtm_label": g.get("label") or None,
            "staff_pick": bool(staff), "staff_pick_label": txt(staff),
            "image_url": img.get("data-src") if img else None,
            "image_url_2x": img2x.group(1) if img2x else None,
            "image_alt": img.get("alt") if img else None,
            "aspect_ratio": float(ar.group(1)) if ar else None,
            "is_featured": f is not None,
            "featured_position": f[0] if f else None,
            "featured_image_url": f[1] if f else None,
            "gtm": g or None,
        })

    # ---- exhibitions
    exhibitions = []
    for pos, card in enumerate(main.select("#exhibitions article.card, #exhibitions .card"), 1):
        a = card.find("a")
        em = EXHIBITION_URL_RE.search(a.get("href", "") if a else "")
        if not em or any(e["exhibition_id"] == int(em.group(1)) for e in exhibitions):
            continue
        body = card.select_one(".card__body")
        ps = body.find_all("p") if body else []
        date_text = clean(re.sub(r"\s+", " ", txt(ps[-1]))) if len(ps) >= 2 else None
        start, end = parse_exhibition_dates(date_text)
        img = card.find("img")
        exhibitions.append({
            "exhibition_id": int(em.group(1)), "position": len(exhibitions) + 1,
            "gallery_name": txt(body.find("h3")) if body else None,
            "name": txt(ps[0]) if ps else (img.get("alt") if img else None),
            "url": abs_url(a["href"]), "image_url": img.get("data-src") if img else None,
            "date_text": date_text, "start_date": start, "end_date": end,
        })

    studio = []
    for pos, card in enumerate(main.select("#studio .card-studio"), 1):
        img = card.find("img")
        studio.append({"position": pos, "image_url": img.get("data-src") if img else None,
                       "caption": txt(card.select_one(".card-studio__text"))})

    movement_cards = []
    for pos, card in enumerate(main.select("#movements .card"), 1):
        a, img = card.find("a"), card.find("img")
        movement_cards.append({"position": pos, "name": txt(card.find("h3")),
                               "url": abs_url(a.get("href")) if a else None,
                               "image_url": img.get("data-src") if img else None})
    # Artists without their own movements get the site-wide list of all ~112 movements;
    # that is not artist data, so drop it and keep a flag instead.
    generic_movements = len(movement_cards) > 20
    if generic_movements:
        movement_cards = []

    similar = []
    for pos, card in enumerate(main.select(".artist-cards__artist-item"), 1):
        g = gtm(card) or {}
        img = card.find("img")
        u = deobfuscate(card.get("data-infos"))
        similar.append({"position": pos,
                        "similar_artist_id": g.get("artist_id") or artist_id_from_url(u),
                        "name": txt(card.select_one(".artist-cards__artist-item-title")) or g.get("artist_name"),
                        "nationality": g.get("artist_nationality"), "url": u,
                        "followers_count": to_int(txt(card.select_one(".artist-cards__artist-item-followers"))),
                        "image_url": (img.get("data-src") or None) if img else None})

    related = []
    for chip in main.select('a.chip[data-label="Tags"]'):
        u = abs_url(chip.get("href"))
        related.append({"position": len(related) + 1, "linked_artist_id": artist_id_from_url(u),
                        "label": txt(chip), "url": u})

    crumbs = [{"position": li.get("position"), "id": (li.get("item") or {}).get("@id"),
               "name": (li.get("item") or {}).get("name")}
              for li in (ld.get("BreadcrumbList") or {}).get("itemListElement", [])]

    title = soup.title.string if soup.title else None
    avail = re.search(r"([\d,]+) works? available", meta.get("description") or "")
    out["artist"] = {
        "artist_id": artist_id,
        "name": person.get("name") or txt(main.select_one("h1")),
        "slug": m.group(3) if m else None, "url": a_url,
        "country_slug": m.group(1) if m else None,
        "nationality": person.get("nationality"),
        "nationality_label": nationality_label, "nationality_url": nationality_url,
        "birth_year": birth_year, "death_year": death_year,
        "followers_count": followers, "quote": quote,
        "description": clean(person.get("description")),
        "biography_text": bio_text, "biography_html": bio_html,
        "image_url": person.get("image"), "cover_image_url": cover_url,
        "artworks_available_count": to_int(avail.group(1)) if avail else None,
        "artworks_on_page": len(artworks),
        "movements_generic_list": generic_movements, "artwork_pages_total": pages_total,
        "page_title": clean(title), "meta_description": meta.get("description"),
        "robots": meta.get("robots"), "og_title": meta.get("og:title"),
        "og_description": meta.get("og:description"), "og_image": meta.get("og:image"),
        "og_locale": meta.get("og:locale"),
        "page_headline": htmllib.unescape((ld.get("WebPage") or {}).get("headline") or "") or None,
        "same_as": person.get("sameAs"), "breadcrumb": crumbs,
        "gtm_page_context": gtm(ctx), "jsonld": jsonld, "meta_tags": meta_tags,
    }
    out.update(selections=selections, attributes=attributes, filters=filters,
               artworks=artworks, exhibitions=exhibitions, studio=studio,
               movement_cards=movement_cards, similar=similar, related=related)
    return out
