"""One mapper for every auction house: the parser's flat lot dict -> a LotRow.

Parsers only include a field when the page has it. Work and artist detail (size, medium, year,
signature, life dates, nationality) the parser leaves out is read from the catalogue text.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from etl_core.normalize import (
    clean_text,
    currency_code,
    plain_text,
    platform_id,
    sane_year,
    to_float,
    to_price,
    to_url,
)
from etl_core.sources import AUCTION_PLATFORMS
from lots import catalogue
from lots.fields import to_date
from lots.schema import LotRow

_STATUSES = frozenset({"sold", "passed", "unsold", "withdrawn", "upcoming", "unknown"})


class LotMapper:
    def is_relevant(self, parsed: Mapping[str, Any]) -> bool:
        return parsed.get("page_type") == "lot"

    def to_rows(
        self, parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None
    ) -> list[LotRow]:
        lot_id = platform_id(parsed.get("platform_lot_id"))
        if lot_id is None:
            return []
        status = clean_text(parsed.get("sale_status"))
        if status is not None:
            status = status.lower()
            if status not in _STATUSES:
                status = None
        title = _line(parsed.get("title"))
        description = plain_text(parsed.get("description"))
        artist = _line(parsed.get("artist_name"))
        born, died, nationality = catalogue.life(artist)
        if (parsed_born := sane_year(parsed.get("artist_birth_year"))) is not None:
            born, died = parsed_born, sane_year(parsed.get("artist_death_year"))
        size = tuple(_cm(parsed.get(f"{side}_cm")) for side in ("height", "width", "depth"))
        if size[0] is None and size[1] is None:
            size = catalogue.size_cm(description)
        signed = parsed.get("is_signed")
        return [
            LotRow(
                platform_lot_id=lot_id,
                lot_url=to_url(parsed.get("lot_url")),
                lot_number=clean_text(parsed.get("lot_number")),
                title=title,
                description=description,
                artist_name=artist,
                category=_line(parsed.get("category")),
                image_url=to_url(parsed.get("image_url")),
                currency=currency_code(parsed.get("currency")),
                low_estimate=_money(parsed.get("low_estimate")),
                high_estimate=_money(parsed.get("high_estimate")),
                price_realized=_money(parsed.get("price_realized")),
                sale_status=status,
                sale_id=platform_id(parsed.get("sale_id")),
                sale_title=_line(parsed.get("sale_title")),
                sale_date=to_date(parsed.get("sale_date")),
                sale_url=to_url(parsed.get("sale_url")),
                selling_house_name=_line(parsed.get("selling_house_name")),
                is_result=_flag(parsed.get("is_result")),
                has_detail=_flag(parsed.get("has_detail")),
                height_cm=size[0],
                width_cm=size[1],
                depth_cm=size[2],
                medium=_line(parsed.get("medium")) or catalogue.medium(description),
                year=sane_year(parsed.get("year")) or catalogue.year(description, title, born),
                is_signed=signed if isinstance(signed, bool) else catalogue.is_signed(description),
                artist_birth_year=born,
                artist_death_year=died,
                artist_nationality=_line(parsed.get("artist_nationality")) or nationality,
                sale_location=_line(parsed.get("sale_location")),
                department=_line(parsed.get("department")),
                source_file=source_file,
                crawled_at=crawled_at,
            )
        ]


def _money(value: object):
    """Positive amount, or None. Zero is not a hammer and not an estimate."""
    price = to_price(value)
    if price is None or price == 0:
        return None
    return price


def _cm(value: object) -> float | None:
    number = to_float(value)
    return round(number, 1) if number is not None and number > 0 else None


def _line(value: object) -> str | None:
    return clean_text(plain_text(value))


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


MAPPERS: dict[str, LotMapper] = {platform: LotMapper() for platform in AUCTION_PLATFORMS}
