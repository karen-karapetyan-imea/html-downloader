"""Read-only structure probe: which data do a platform's crawled pages expose?

Samples HTML files from one crawl folder, groups them by page type (from the page URL) and reports,
per group, the JSON-LD types and keys, embedded JSON blobs, meta tags and data-looking CSS classes
the pages carry: how many sampled pages have each one, plus one sample value. A few trimmed example
pages per group are written next to the report so they can be shared without the whole crawl.

The crawl folder is never modified; everything goes to --out (default /tmp/probe/<platform>/).

    uv run python scripts/probe_structure.py --platform singulart --data /data/singulart/2026-10-14
    tar czf probe-singulart.tgz -C /tmp/probe singulart
"""

import argparse
import itertools
import json
import random
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from etl_core.crawl import LogEntry, html_root, iter_html_files, load_crawl_log

type Pattern = tuple[str, re.Pattern[str]]

PAGE_PATTERNS: dict[str, tuple[Pattern, ...]] = {
    "artsper": (
        ("artwork", re.compile(r"/contemporary-artworks/[^/]+/\d+/")),
        ("artist", re.compile(r"/contemporary-artists/[^/]+/\d+/")),
    ),
    "fineartamerica": (
        ("artwork", re.compile(r"/featured/[^/?#]+\.html/?$")),
        ("artist", re.compile(r"/profiles/[^/?#]+/?$")),
    ),
    "mutualart": (
        ("artwork", re.compile(r"/Artwork/", re.IGNORECASE)),
        ("artist", re.compile(r"/Artist/[^/?#]+/[0-9A-F]{10,}/?$", re.IGNORECASE)),
        ("organization", re.compile(r"/Organization/", re.IGNORECASE)),
        ("exhibition", re.compile(r"/Exhibition/", re.IGNORECASE)),
        ("auction", re.compile(r"/Auction/", re.IGNORECASE)),
    ),
    "phaidon": (("product", re.compile(r"/products/[^/?#]+/?$")),),
    "singulart": (
        ("artwork", re.compile(r"/artworks/[^/?#]+-\d+/?$")),
        ("artist", re.compile(r"/artist/[^/?#]+-\d+/?$")),
    ),
    "ugallery": (
        ("artwork", re.compile(r"/products/[^/?#]+/?$")),
        ("artist", re.compile(r"/pages/[^/?#]+/?$")),
    ),
}

MAX_DEPTH = 8
MAX_PATHS_PER_BLOB = 400
MIN_JS_BLOB_PATHS = 5  # minified bundles assign tiny objects to one-letter names; state blobs are big
LIST_ITEMS = 2
SAMPLE_CHARS = 90
SECTION_LIMITS = {"css classes": 60, "meta": 80}
DEFAULT_SECTION_LIMIT = 250
DATA_CLASS_RE = re.compile(
    r"artist|artwork|price|product|bio|dimension|medium|size|year|national|country|location|"
    r"follow|social|sold|avail|title|descr|categ|style|subject|material|technique|edition|lot|estimate",
    re.IGNORECASE,
)
JS_ASSIGN_RE = re.compile(r"(?:window\.|self\.|\bvar\s+|\blet\s+|\bconst\s+)([\w$.]+)\s*=\s*(?=[{\[])")
DROP_TAGS = ("style", "svg", "noscript", "iframe", "link", "picture", "source")
_DECODER = json.JSONDecoder()

type Features = dict[str, dict[str, str]]  # section -> feature -> sample value


@dataclass(slots=True)
class Section:
    counts: Counter[str] = field(default_factory=Counter)
    samples: dict[str, str] = field(default_factory=dict)

    def add(self, features: dict[str, str]) -> None:
        self.counts.update(features.keys())
        for key, sample in features.items():
            self.samples.setdefault(key, sample)


@dataclass(slots=True)
class Group:
    pages: int = 0
    examples: list[str] = field(default_factory=list)
    sections: defaultdict[str, Section] = field(default_factory=lambda: defaultdict(Section))

    def add(self, features: Features) -> None:
        self.pages += 1
        for name, values in features.items():
            self.sections[name].add(values)


# ---- sampling and classification


def sample_files(root: Path, size: int, seed: int) -> list[Path]:
    """Reservoir sample: one pass over the crawl, memory bounded by the sample size."""
    rng = random.Random(seed)
    reservoir: list[Path] = []
    for i, path in enumerate(iter_html_files(root)):
        if i < size:
            reservoir.append(path)
        elif (j := rng.randrange(i + 1)) < size:
            reservoir[j] = path
    return sorted(reservoir)


def page_url(soup: BeautifulSoup, entry: LogEntry | None) -> str | None:
    if entry is not None and entry.url:
        return entry.url
    canonical = soup.find("link", rel="canonical")
    if isinstance(canonical, Tag) and canonical.get("href"):
        return str(canonical["href"])
    og_url = soup.find("meta", property="og:url")
    if isinstance(og_url, Tag) and og_url.get("content"):
        return str(og_url["content"])
    return None


def classify(platform: str, url: str | None) -> str:
    if not url:
        return "no-url"
    path = url.split("?", 1)[0].split("#", 1)[0]
    for group, pattern in PAGE_PATTERNS.get(platform, ()):
        if pattern.search(path):
            return group
    return "other"


# ---- feature extraction


def sample_value(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= SAMPLE_CHARS else text[: SAMPLE_CHARS - 1] + "…"


def leaf_paths(value: Any, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and value and depth < MAX_DEPTH:
        for key, item in value.items():
            yield from leaf_paths(item, f"{prefix}.{key}" if prefix else str(key), depth + 1)
    elif isinstance(value, list) and value and depth < MAX_DEPTH:
        for item in value[:LIST_ITEMS]:
            yield from leaf_paths(item, f"{prefix}[]", depth + 1)
    else:
        yield prefix or "(root)", value


def blob_features(blob: Any) -> dict[str, str]:
    features: dict[str, str] = {}
    for path, value in itertools.islice(leaf_paths(blob), MAX_PATHS_PER_BLOB):
        features.setdefault(path, sample_value(value))
    return features


def load_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except ValueError:
        return None


def jsonld_objects(data: Any) -> Iterator[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from jsonld_objects(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from jsonld_objects(data["@graph"])
        else:
            yield data


def ld_type(obj: dict[str, Any]) -> str:
    kind = obj.get("@type")
    return "/".join(map(str, kind)) if isinstance(kind, list) else str(kind or "untyped")


def script_name(script: Tag) -> str:
    if script.get("id"):
        return f"script#{script['id']}"
    data_attrs = sorted(k for k in script.attrs if k.startswith("data-"))
    return f"script[{data_attrs[0]}]" if data_attrs else f"script[type={script.get('type')}]"


def embedded_json(soup: BeautifulSoup) -> Iterator[tuple[str, Any]]:
    """JSON islands (`<script type=application/json>`, __NEXT_DATA__) and `window.X = {...}` assignments."""
    for script in soup.find_all("script"):
        if not isinstance(script, Tag) or script.get("src"):
            continue
        kind = str(script.get("type") or "").lower()
        text = script.string or script.get_text() or ""
        if kind == "application/ld+json":
            continue
        if "json" in kind:
            if (data := load_json(text)) is not None:
                yield script_name(script), data
            continue
        for match in JS_ASSIGN_RE.finditer(text):
            try:
                data, _ = _DECODER.raw_decode(text, match.end())
            except ValueError:
                continue
            yield f"js {match.group(1)}", data


def page_features(soup: BeautifulSoup, url: str | None) -> Features:
    features: Features = defaultdict(dict)
    h1 = soup.find("h1")
    features["page"] = {
        "url": sample_value(url or ""),
        "title": sample_value(soup.title.get_text() if soup.title else ""),
        "h1": sample_value(h1.get_text(" ", strip=True) if h1 else ""),
    }
    for script in soup.find_all("script", type="application/ld+json"):
        for obj in jsonld_objects(load_json(script.string or script.get_text() or "")):
            kind = ld_type(obj)
            features["json-ld types"][kind] = kind
            features[f"json-ld {kind}"].update(blob_features(obj))
    for name, blob in embedded_json(soup):
        values = blob_features(blob)
        if name.startswith("js ") and len(values) < MIN_JS_BLOB_PATHS:
            continue
        features[f"embedded json {name}"].update(values)
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name") or meta.get("itemprop")
        if key and meta.get("content") is not None:
            features["meta"].setdefault(str(key), sample_value(meta["content"]))
    for tag in soup.find_all(class_=DATA_CLASS_RE):
        for css in tag.get("class") or []:
            if DATA_CLASS_RE.search(css):
                features["css classes"].setdefault(css, sample_value(tag.get_text(" ", strip=True)))
    return features


def trimmed_html(soup: BeautifulSoup) -> str:
    """Drop styling and executable scripts; keep JSON data scripts, which parsers read."""
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for script in soup.find_all("script"):
        if "json" not in str(script.get("type") or "").lower():
            script.decompose()
    return str(soup)


# ---- report


def section_order(name: str) -> tuple[int, str]:
    prefixes = ("page", "json-ld types", "json-ld ", "embedded json ", "meta", "css classes")
    rank = next((i for i, p in enumerate(prefixes) if name.startswith(p)), len(prefixes))
    return rank, name


def render_report(
    platform: str, data_dir: Path, groups: dict[str, Group], errors: int, min_share: float
) -> str:
    total = sum(g.pages for g in groups.values())
    lines = [f"# probe platform={platform} data={data_dir} sampled={total} errors={errors}"]
    lines.append("# groups: " + ", ".join(f"{name}={g.pages}" for name, g in sorted(groups.items())))
    for name, group in sorted(groups.items(), key=lambda kv: -kv[1].pages):
        lines += ["", f"=== group={name} pages={group.pages}", f"examples: {', '.join(group.examples)}"]
        threshold = max(1, round(group.pages * min_share))
        for section_name in sorted(group.sections, key=section_order):
            section = group.sections[section_name]
            rows = [(k, c) for k, c in section.counts.items() if c >= threshold or section_name == "page"]
            if not rows:
                continue
            limit = SECTION_LIMITS.get(section_name, DEFAULT_SECTION_LIMIT)
            rows.sort(key=lambda kc: (-kc[1], kc[0]))
            lines.append(f"--- {section_name} ({len(rows)} keys)")
            lines += [f"{count:>5}  {key} = {section.samples[key]}" for key, count in rows[:limit]]
            if len(rows) > limit:
                lines.append(f"      ... {len(rows) - limit} more")
    return "\n".join(lines) + "\n"


# ---- main


def probe(args: argparse.Namespace) -> int:
    data_dir = Path(args.data).expanduser()
    root = html_root(data_dir)
    if not root.is_dir():
        print(f"error: {data_dir} is not a crawl folder", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser() / args.platform
    crawl_log = load_crawl_log(data_dir)
    groups: dict[str, Group] = defaultdict(Group)
    errors = 0
    for path in sample_files(root, args.sample, args.seed):
        try:
            soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
            url = page_url(soup, crawl_log.get(path.name))
            group_name = classify(args.platform, url)
            group = groups[group_name]
            group.add(page_features(soup, url))
            if len(group.examples) < args.examples:
                target = out_dir / "examples" / group_name / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(trimmed_html(soup), encoding="utf-8")
                group.examples.append(path.name)
        except Exception as exc:  # a broken page must not stop the probe
            errors += 1
            print(f"warning: {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    report = render_report(args.platform, data_dir, groups, errors, args.min_share)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"report and examples written to {out_dir}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--platform", required=True, help=f"known URL patterns: {', '.join(PAGE_PATTERNS)}")
    parser.add_argument("--data", required=True, help="one crawl folder (<data>/<platform>/<YYYY-MM-DD>)")
    parser.add_argument("--sample", type=int, default=200, help="HTML files to analyse")
    parser.add_argument("--examples", type=int, default=3, help="trimmed example pages to keep per group")
    parser.add_argument("--min-share", type=float, default=0.05, help="hide keys on fewer pages than this")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="/tmp/probe")
    return parser


def main(argv: list[str] | None = None) -> int:
    return probe(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
