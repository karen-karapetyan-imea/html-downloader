"""JSON-LD blocks of an HTML page as flat objects (lists and @graph unwrapped)."""

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

type LdObject = dict[str, Any]


def flatten(data: Any) -> Iterator[LdObject]:
    if isinstance(data, list):
        for item in data:
            yield from flatten(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from flatten(data["@graph"])
        else:
            yield data


def jsonld_objects(soup: BeautifulSoup) -> list[LdObject]:
    """Every object of every parsable JSON-LD script; broken blocks are skipped."""
    objects: list[LdObject] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            objects.extend(flatten(json.loads(script.string or script.get_text() or "")))
        except ValueError:
            continue
    return objects


def ld_types(obj: LdObject) -> set[str]:
    kind = obj.get("@type")
    return {str(k) for k in kind} if isinstance(kind, list) else ({str(kind)} if kind else set())


def first_of_type(objects: list[LdObject], *types: str) -> LdObject | None:
    wanted = set(types)
    return next((o for o in objects if ld_types(o) & wanted), None)
