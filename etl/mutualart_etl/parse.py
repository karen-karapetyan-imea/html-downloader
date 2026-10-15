"""Parse one MutualArt artist page (raw HTML): the artist and the artwork cards it lists.

Everything comes from JSON-LD. The artist is the Person whose @id is the page URL + "#artist".
Artworks come from the page's "#artworks" (for sale) and "#auction-results" ItemLists; other lists
(exhibitions, articles) are not artworks. Prices and estimates are behind a login and not on the page.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

from etl_core.jsonld import LdObject, first_of_type, jsonld_objects, ld_types

ARTIST_URL_RE = re.compile(r"/Artist/[^/?#]+/([0-9A-Fa-f]{10,})")
ARTWORK_URL_RE = re.compile(r"/Artwork/[^/?#]+/([0-9A-Fa-f]{10,})")
# list fragment -> is the artwork for sale; a past auction result says nothing about today
ARTWORK_LISTS: dict[str, bool | None] = {"artworks": True, "auction-results": None}


def hex_id(pattern: re.Pattern[str], url: object) -> str | None:
    m = pattern.search(url) if isinstance(url, str) else None
    return m.group(1).upper() if m else None


def ref_id(value: object) -> object:
    return value.get("@id") if isinstance(value, dict) else None


def strip_year(title: object, year: object) -> object:
    """Titles repeat the creation year as a ", 2016" suffix; dateCreated already holds it."""
    if not isinstance(title, str) or not year:
        return title
    return re.sub(rf",\s*{re.escape(str(year))}\s*$", "", title)


def parse_page(html: str) -> dict[str, Any]:
    objects = jsonld_objects(BeautifulSoup(html, "lxml"))
    person = next(
        (o for o in objects if "Person" in ld_types(o) and str(o.get("@id", "")).endswith("#artist")), None
    )
    artist_id = hex_id(ARTIST_URL_RE, person.get("@id")) if person else None
    if person is None or artist_id is None:
        return {"page_type": "unknown", "rows": {}}
    profile = first_of_type(objects, "ProfilePage") or {}
    artworks = [
        row for obj in objects if "ItemList" in ld_types(obj) for row in _list_artworks(obj, artist_id)
    ]
    return {
        "page_type": "artist",
        "artist_id": artist_id,
        "rows": {"artists": [_artist(person, profile, artist_id)], "artworks": artworks},
    }


def _artist(person: LdObject, profile: LdObject, artist_id: str) -> dict[str, Any]:
    image = profile.get("primaryImageOfPage")
    return {
        "artist_id": artist_id,
        "name": person.get("name"),
        "given_name": person.get("givenName"),
        "family_name": person.get("familyName"),
        "url": profile.get("url") or str(person.get("@id", "")).split("#", 1)[0],
        "avatar_url": (image.get("contentUrl") or image.get("url")) if isinstance(image, dict) else None,
        "birth_date": person.get("birthDate"),
        "death_date": person.get("deathDate"),
        "nationality": person.get("nationality"),
        "description": person.get("description"),
        "has_page": True,
    }


def _list_artworks(item_list: LdObject, page_artist_id: str) -> list[dict[str, Any]]:
    list_id = str(item_list.get("@id") or "")
    page, _, fragment = list_id.partition("#")
    if fragment not in ARTWORK_LISTS:
        return []
    # The list belongs to the page artist, so a card without its own artist ref is theirs.
    list_artist_id = hex_id(ARTIST_URL_RE, page) or page_artist_id
    rows = []
    for element in item_list.get("itemListElement") or []:
        item = element.get("item") if isinstance(element, dict) else None
        if not isinstance(item, dict) or "VisualArtwork" not in ld_types(item):
            continue
        rows.append(
            {
                "artwork_id": hex_id(ARTWORK_URL_RE, item.get("url")),
                "artist_id": hex_id(ARTIST_URL_RE, ref_id(item.get("artist"))) or list_artist_id,
                "title": strip_year(item.get("name"), item.get("dateCreated")),
                "url": item.get("url"),
                "image_url": item.get("image"),
                "art_medium": item.get("artMedium"),
                "surface": item.get("artworkSurface"),
                "date_created": item.get("dateCreated"),
                "description": item.get("description"),
                "for_sale": ARTWORK_LISTS[fragment],
                "list": fragment,
                "has_detail": False,
            }
        )
    return rows
