"""Registry of the per-platform parsers that already live next to this package.

The team parsers are loaded straight from their files: `artsper-etl` is not a valid
module name and all five files are called `parse.py`, so each one gets a unique name.
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
        Source("saatchi", ETL_ROOT / "saatchi_etl" / "parse.py"),
    )
}
PLATFORMS: tuple[str, ...] = tuple(sorted(SOURCES))


def get_source(platform: str) -> Source:
    try:
        return SOURCES[platform]
    except KeyError:
        raise ValueError(f"unknown platform {platform!r}, expected one of {', '.join(PLATFORMS)}") from None


@cache
def load_parser(platform: str) -> ModuleType:
    source = get_source(platform)
    name = f"_artist_etl_parser_{platform}"
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
