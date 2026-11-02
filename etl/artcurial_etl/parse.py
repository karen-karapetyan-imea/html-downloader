"""Artcurial lot pages: Nuxt `__NUXT_DATA__` on `/en/sales/{ref}/lots/{index}-{sub}`.

`adjudicationPrice` is the hammer. `finalPrice` includes fees, so it is used only when the
hammer is missing. Lot id is `{saleRef}:{index}-{subIndex}`, matching the downloader.
Dates arrive as devalue `["Date", iso]` pairs, which `decode_nuxt_data` turns into the iso string.

Work detail: `technique` (medium) and `signature` sit in the description block; `properties`
holds sizes, mostly zero. The sale location is the first session's city ("Artcurial, Paris"),
the department the sale's principal specialty ("Tableaux Anciens").
"""

from __future__ import annotations

from typing import Any

from lots import catalogue
from lots.embedded import script_json
from lots.fields import dig, lot_page, sale_status, to_date, unknown_page
from lots.nuxt import decode_nuxt_data

_LOCALES = ("ENGLISH", "FRENCH", "en", "fr")
_TO_CM = {"CM": 1.0, "MM": 0.1, "M": 100.0}


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    payload = script_json(html, "__NUXT_DATA__")
    if not isinstance(payload, list):
        return unknown_page()
    root = decode_nuxt_data(payload)
    data = root.get("data") if isinstance(root, dict) else None
    if not isinstance(data, dict):
        return unknown_page()
    lot = _lot_record(data.get("lot"))
    if lot is None:
        return unknown_page()
    sale = data.get("sale-lot-page")
    sale = sale if isinstance(sale, dict) else {}
    description = _description(lot.get("descriptions"))
    sale_ref = _text(lot.get("saleRef")) or _text(sale.get("ref"))
    index = lot.get("index")
    sub = _text(lot.get("subIndex"))
    status = _text(lot.get("status"))
    sold = status is not None and status.upper() == "SOLD"
    born, died = dig(lot, "artist", "yearOfBirth"), dig(lot, "artist", "yearOfDeath")
    sizes = lot.get("properties") if isinstance(lot.get("properties"), dict) else {}
    return lot_page(
        platform_lot_id=_lot_id(sale_ref, index, sub, _text(lot.get("uuid"))),
        lot_url=url,
        lot_number=_lot_number(index, sub),
        title=description.get("titleWithHtml"),
        description=description.get("descriptionWithHtml"),
        artist_name=_artist(lot),
        category=_text(lot.get("category")),
        image_url=_image(lot.get("pictures")),
        currency=_text(lot.get("currency")),
        low_estimate=lot.get("low"),
        high_estimate=lot.get("high"),
        price_realized=_price(lot, sold),
        sale_status=sale_status(status, sold=sold),
        sale_id=sale_ref,
        sale_title=_text(sale.get("name")),
        sale_date=to_date(sale.get("effectiveDate") or lot.get("adjudicationDate")),
        sale_url=_sale_url(sale_ref),
        height_cm=_size(sizes, "height"),
        width_cm=_size(sizes, "width"),
        depth_cm=_size(sizes, "depth"),
        medium=_text(description.get("technique")),
        is_signed=catalogue.is_signed(_text(description.get("signature"))),
        artist_birth_year=born if _year(born) else None,
        artist_death_year=died if _year(born) and _year(died) else None,
        sale_location=_city(sale.get("vacations")),
        department=_specialty(sale.get("specialties")),
        is_result=sold,
    )


def _size(properties: dict[str, Any], side: str) -> float | None:
    value = properties.get(side)
    factor = _TO_CM.get(str(properties.get(f"{side}Unit") or "").upper())
    if factor is None or isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return value * factor


def _city(vacations: object) -> str | None:
    """City of the first session, without the saleroom name: "Artcurial, Paris" -> "Paris"."""
    if not isinstance(vacations, list) or not vacations:
        return None
    city = _text(dig(vacations[0], "address", "city"))
    return city.rsplit(",", 1)[-1].strip() or None if city else None


def _specialty(specialties: object) -> str | None:
    if not isinstance(specialties, list):
        return None
    for specialty in specialties:
        if dig(specialty, "principal") is True:
            return _text(dig(specialty, "specialty", "name"))
    return None


def _lot_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    inner = value.get("lot")
    record = inner if isinstance(inner, dict) else value
    if record.get("uuid") is None and record.get("saleRef") is None:
        return None
    return record


def _artist(lot: dict[str, Any]) -> str | None:
    """`Name (dates)` like Christie's: the catalogue's `artistName`/`artistDates`, else the
    referential artist (`artist.party.fullName`, `yearOfBirth`, `yearOfDeath`)."""
    name = _text(lot.get("artistName")) or _text(dig(lot, "artist", "party", "fullName"))
    if name is None:
        return None
    dates = (_text(lot.get("artistDates")) or "").strip("() ") or None
    if dates is None:
        born, died = dig(lot, "artist", "yearOfBirth"), dig(lot, "artist", "yearOfDeath")
        if _year(born) and _year(died):
            dates = f"{born}-{died}"
        elif _year(born):
            dates = f"b. {born}"
    return f"{name} ({dates})" if dates else name


def _year(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _lot_id(sale_ref: str | None, index: object, sub: str | None, uuid: str | None) -> str | None:
    if sale_ref and isinstance(index, int) and not isinstance(index, bool):
        suffix = f"{index}-{sub.lower()}" if sub else str(index)
        return f"{sale_ref}:{suffix}"
    return uuid


def _lot_number(index: object, sub: str | None) -> str | None:
    if isinstance(index, bool) or not isinstance(index, int):
        return sub
    return f"{index}{sub}" if sub else str(index)


def _price(lot: dict[str, Any], sold: bool) -> object:
    if not sold:
        return None
    hammer = lot.get("adjudicationPrice")
    if isinstance(hammer, int | float) and not isinstance(hammer, bool) and hammer > 0:
        return hammer
    final = lot.get("finalPrice")
    if isinstance(final, int | float) and not isinstance(final, bool) and final > 0:
        return final
    return None


def _description(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in _LOCALES:
        block = value.get(key)
        if isinstance(block, dict):
            return block
    for block in value.values():
        if isinstance(block, dict) and dig(block, "titleWithHtml") is not None:
            return block
    return {}


def _image(pictures: object) -> str | None:
    if not isinstance(pictures, list):
        return None
    for picture in pictures:
        url = dig(picture, "document", "servingUrl")
        if isinstance(url, str) and url:
            return url
    return None


def _sale_url(sale_ref: str | None) -> str | None:
    if not sale_ref:
        return None
    return f"https://www.artcurial.com/en/sales/{sale_ref}"


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
