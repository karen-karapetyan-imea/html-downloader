"""In-memory Algolia stand-in that evaluates the filter strings discovery builds."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

_RANGE = re.compile(r"^(NOT )?([\w.]+):(-?[\d.]+) TO (-?[\d.]+)$")
_COMPARE = re.compile(r"^([\w.]+)\s*(>=|<|=)\s*(-?[\d.]+)$")
_FACET = re.compile(r'^(NOT )?([\w.]+):(?:"((?:[^"\\]|\\.)*)"|(\S+))$')


def get_attr(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _facet_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _match_clause(record: dict[str, Any], clause: str) -> bool:
    clause = clause.strip()
    if clause.startswith("(") and clause.endswith(")"):
        return any(_match_clause(record, part) for part in clause[1:-1].split(" OR "))
    if m := _RANGE.match(clause):
        negate, attr, lo, hi = m.groups()
        value = get_attr(record, attr)
        inside = isinstance(value, int | float) and float(lo) <= value <= float(hi)
        return not inside if negate else inside
    if m := _COMPARE.match(clause):
        attr, op, raw = m.groups()
        value = get_attr(record, attr)
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        bound = float(raw)
        return {">=": value >= bound, "<": value < bound, "=": value == bound}[op]
    if m := _FACET.match(clause):
        negate, attr, quoted, bare = m.groups()
        wanted = quoted.replace('\\"', '"').replace("\\\\", "\\") if quoted is not None else bare
        value = get_attr(record, attr)
        values = value if isinstance(value, list) else [value]
        hit = any(v is not None and _facet_value(v) == wanted for v in values)
        return not hit if negate else hit
    raise AssertionError(f"unsupported clause {clause!r}")


def matches(record: dict[str, Any], filters: str) -> bool:
    return all(_match_clause(record, c) for c in filters.split(" AND ") if c.strip())


def facet_counts(records: Sequence[dict[str, Any]], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = get_attr(record, attr)
        for item in value if isinstance(value, list) else [value]:
            if item is not None:
                key = _facet_value(item)
                counts[key] = counts.get(key, 0) + 1
    return counts


def respond(
    records: Sequence[dict[str, Any]],
    params: dict[str, Any],
    *,
    facets: bool = True,
    exact: bool | None = None,
    page_cap: int | None = None,
    nb_hits: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    """Algolia-shaped response: ``nbHits``, capped ``hits``, optional facets / exhaustive."""
    matched = [r for r in records if matches(r, params.get("filters") or "")]
    per_page = int(params.get("hitsPerPage", 20))
    if page_cap is not None:
        per_page = min(per_page, page_cap)
    body: dict[str, Any] = {
        "nbHits": nb_hits(len(matched)) if nb_hits else len(matched),
        "hits": [dict(r) for r in matched[:per_page]],
    }
    if exact is not None:
        body["exhaustive"] = {"nbHits": exact}
    attr = params.get("facets")
    if attr and facets:
        body["facets"] = {attr: facet_counts(matched, attr)}
    return body


class FakeIndex:
    """One index: records, call log and failure injection."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        facets: bool = True,
        exact: bool | None = None,
        page_cap: int | None = None,
        nb_hits: Callable[[int], int] | None = None,
    ) -> None:
        self.records = records
        self.facets = facets
        self.exact = exact
        self.page_cap = page_cap
        self.nb_hits = nb_hits
        self.calls: list[dict[str, Any]] = []
        self.fail_after: int | None = None
        self.fail_when: str | None = None

    def query(self, _index: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append(params)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return None
        if self.fail_when and self.fail_when in (params.get("filters") or ""):
            return None
        return respond(
            self.records,
            params,
            facets=self.facets,
            exact=self.exact,
            page_cap=self.page_cap,
            nb_hits=self.nb_hits,
        )

    @property
    def hit_queries(self) -> list[dict[str, Any]]:
        return [p for p in self.calls if int(p.get("hitsPerPage", 0)) > 0]
