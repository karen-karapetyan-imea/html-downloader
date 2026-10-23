"""Registry of the per-platform parsers that already live next to this package.

The parsers are loaded straight from their files: `artsper-etl` is not a valid
module name and every file is called `parse.py`, so each one gets a unique name.
"""

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

ETL_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Source:
    platform: str
    parser_path: Path
    accepts_url: bool = False  # parse_page(html, url) instead of parse_page(html)


SOURCES: dict[str, Source] = {
    s.platform: s
    for s in (
        Source("artfinder", ETL_ROOT / "artfinder_etl" / "parse.py"),
        Source("artmajeur", ETL_ROOT / "artmajeur_etl" / "parse.py"),
        Source("artsper", ETL_ROOT / "artsper-etl" / "parse.py", accepts_url=True),
        Source("artsy", ETL_ROOT / "artsy_etl" / "parse.py"),
        Source("fineartamerica", ETL_ROOT / "fineartamerica_etl" / "parse.py"),
        Source("mutualart", ETL_ROOT / "mutualart_etl" / "parse.py"),
        Source("phaidon", ETL_ROOT / "phaidon_etl" / "parse.py"),
        Source("saatchi", ETL_ROOT / "saatchi_etl" / "parse.py"),
        Source("ugallery", ETL_ROOT / "ugallery_etl" / "parse.py"),
    )
}
# Marketplace crawls only. Auction houses live in AUCTION_SOURCES so artist/artwork
# datasets, which iterate PLATFORMS, do not try to map a lot page as an artist.
PLATFORMS: tuple[str, ...] = tuple(sorted(SOURCES))

AUCTION_SOURCES: dict[str, Source] = {
    s.platform: s
    for s in (
        Source("artcurial", ETL_ROOT / "artcurial_etl" / "parse.py", accepts_url=True),
        Source("barnebys", ETL_ROOT / "barnebys_etl" / "parse.py", accepts_url=True),
        Source("christies", ETL_ROOT / "christies_etl" / "parse.py", accepts_url=True),
        Source("drouot", ETL_ROOT / "drouot_etl" / "parse.py", accepts_url=True),
        Source("invaluable", ETL_ROOT / "invaluable_etl" / "parse.py", accepts_url=True),
        Source("liveauctioneers", ETL_ROOT / "liveauctioneers_etl" / "parse.py", accepts_url=True),
        Source("saleroom", ETL_ROOT / "saleroom_etl" / "parse.py", accepts_url=True),
        Source("sothebys", ETL_ROOT / "sothebys_etl" / "parse.py", accepts_url=True),
    )
}
AUCTION_PLATFORMS: tuple[str, ...] = tuple(sorted(AUCTION_SOURCES))
ALL_SOURCES: dict[str, Source] = {**SOURCES, **AUCTION_SOURCES}


def get_source(platform: str) -> Source:
    try:
        return ALL_SOURCES[platform]
    except KeyError:
        names = ", ".join(sorted(ALL_SOURCES))
        raise ValueError(f"unknown platform {platform!r}, expected one of {names}") from None


@cache
def load_parser(platform: str) -> ModuleType:
    source = get_source(platform)
    name = f"_etl_parser_{platform}"
    spec = importlib.util.spec_from_file_location(name, source.parser_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load parser for {platform} from {source.parser_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def parse_html(platform: str, html: str, url: str | None = None) -> dict[str, Any]:
    parse_page = load_parser(platform).parse_page
    if get_source(platform).accepts_url:
        return parse_page(html, url)
    return parse_page(html)


@cache
def parser_fingerprint(platform: str) -> str:
    return hashlib.sha256(get_source(platform).parser_path.read_bytes()).hexdigest()[:16]
