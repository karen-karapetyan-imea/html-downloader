"""Shared lot-page helpers: dates, status, and the dict parsers hand to the mapper."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

_SOLD = frozenset({"sold", "resultvisible"})
_PASSED = frozenset({"passed", "boughtin", "bi"})
_UNSOLD = frozenset({"unsold", "closed", "finished"})
_WITHDRAWN = frozenset({"withdrawn", "withdrawnlot"})
_UPCOMING = frozenset(
    {
        "upcoming",
        "created",
        "open",
        "opened",
        "published",
        "live",
        "preview",
        "waitingtobeoffered",
        "none",
    }
)
_EPOCH_MS = 10**11
_EPOCH_S = 10**9


def lot_page(**fields: Any) -> dict[str, Any]:
    """A parsed lot page. Callers fill only the fields the payload actually has."""
    page: dict[str, Any] = {"page_type": "lot", "has_detail": True, "is_result": False}
    page.update(fields)
    return page


def unknown_page() -> dict[str, Any]:
    return {"page_type": "unknown"}


def sale_status(
    raw: object = None,
    *,
    sold: bool | None = None,
    passed: bool | None = None,
    withdrawn: bool | None = None,
    closed: bool | None = None,
) -> str:
    """One of sold, passed, unsold, withdrawn, upcoming, unknown."""
    if withdrawn:
        return "withdrawn"
    key = _key(raw)
    if key in _WITHDRAWN:
        return "withdrawn"
    if key in _SOLD or sold:
        return "sold"
    if key in _PASSED or passed:
        return "passed"
    if key in _UNSOLD:
        return "unsold"
    if key in _UPCOMING:
        return "upcoming"
    if closed:
        return "unsold"
    if sold is False or passed is False:
        return "upcoming"
    return "unknown"


def to_date(value: object) -> date | None:
    """ISO date/datetime, or unix seconds/milliseconds, as a calendar date. Timezone is UTC."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return _from_epoch(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return _from_epoch(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _from_epoch(value: int | float) -> date | None:
    seconds = value / 1000 if abs(value) >= _EPOCH_MS else value
    if abs(seconds) < _EPOCH_S:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value.lower() if char.isalnum())


def first_dict(value: object, *keys: str) -> dict[str, Any]:
    """First nested dict under `keys`, or `value` itself when it is already a dict."""
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def dig(value: object, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
