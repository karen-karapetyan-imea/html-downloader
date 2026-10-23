"""One mapper for every auction house: the parser's flat lot dict -> a LotRow.

Parsers only include a field when the page has it. The mapper does not fill gaps.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from etl_core.normalize import clean_text, currency_code, plain_text, platform_id, to_price, to_url
from etl_core.sources import AUCTION_PLATFORMS
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
        return [
            LotRow(
                platform_lot_id=lot_id,
                lot_url=to_url(parsed.get("lot_url")),
                lot_number=clean_text(parsed.get("lot_number")),
                title=_line(parsed.get("title")),
                description=plain_text(parsed.get("description")),
                artist_name=_line(parsed.get("artist_name")),
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


def _line(value: object) -> str | None:
    return clean_text(plain_text(value))


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


MAPPERS: dict[str, LotMapper] = {platform: LotMapper() for platform in AUCTION_PLATFORMS}
