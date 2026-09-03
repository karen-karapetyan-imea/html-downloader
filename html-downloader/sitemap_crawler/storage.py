"""State persistence and output for the sitemap crawler."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from sitemap_crawler.entities import classify_url, merge_entity_url
from sitemap_crawler.parser import ART_SITEMAP_SEEDS, is_art_sitemap_page

LOGGER = logging.getLogger(__name__)

VISITED_SITEMAPS_FILE = "visited_sitemaps.txt"
DISCOVERED_URLS_FILE = "discovered_urls.txt"
PENDING_SITEMAPS_FILE = "pending_sitemaps.txt"
ARTWORK_URLS_FILE = "artwork_urls.txt"
DEALER_URLS_FILE = "dealer_urls.txt"
CREATOR_URLS_FILE = "creator_urls.txt"

OUTPUT_ARTWORK = "artwork_urls.txt"
OUTPUT_DEALER = "dealer_urls.txt"
OUTPUT_CREATOR = "creator_urls.txt"
OUTPUT_ALL = "all_art_urls.txt"

# Legacy output names kept for backward compatibility in tests.
OUTPUT_TXT = "1stdibs_sitemap_urls.txt"
OUTPUT_JSON = "1stdibs_sitemap_urls.json"


def load_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    values: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            text = line.strip()
            if text and not text.startswith("#"):
                values.add(text)
    return values


def save_lines(path: Path, values: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for value in sorted(values):
                handle.write(f"{value}\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


class CrawlerState:
    """Resume state for art sitemap BFS."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.visited_sitemaps_path = state_dir / VISITED_SITEMAPS_FILE
        self.discovered_urls_path = state_dir / DISCOVERED_URLS_FILE
        self.pending_sitemaps_path = state_dir / PENDING_SITEMAPS_FILE
        self.artwork_urls_path = state_dir / ARTWORK_URLS_FILE
        self.dealer_urls_path = state_dir / DEALER_URLS_FILE
        self.creator_urls_path = state_dir / CREATOR_URLS_FILE
        self.visited_sitemap_pages: set[str] = set()
        self.discovered_urls: set[str] = set()
        self.pending_sitemap_pages: list[str] = []
        self.artwork_urls: dict[str, str] = {}
        self.dealer_urls: dict[str, str] = {}
        self.creator_urls: dict[str, str] = {}

    def load(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.visited_sitemap_pages = load_lines(self.visited_sitemaps_path)
        self.discovered_urls = load_lines(self.discovered_urls_path)
        pending = load_lines(self.pending_sitemaps_path)
        self.pending_sitemap_pages = sorted(
            url for url in pending if is_art_sitemap_page(url)
        )
        self._load_entity_urls(self.artwork_urls_path, self.artwork_urls)
        self._load_entity_urls(self.dealer_urls_path, self.dealer_urls)
        self._load_entity_urls(self.creator_urls_path, self.creator_urls)
        self._migrate_legacy_discovered_urls()
        self._seed_art_roots_if_needed()
        LOGGER.info(
            "loaded state visited_sitemaps=%s pending=%s artwork=%s dealer=%s creator=%s",
            len(self.visited_sitemap_pages),
            len(self.pending_sitemap_pages),
            len(self.artwork_urls),
            len(self.dealer_urls),
            len(self.creator_urls),
        )

    def save(self) -> None:
        save_lines(self.visited_sitemaps_path, self.visited_sitemap_pages)
        save_lines(self.pending_sitemaps_path, set(self.pending_sitemap_pages))
        save_lines(self.artwork_urls_path, set(self.artwork_urls.values()))
        save_lines(self.dealer_urls_path, set(self.dealer_urls.values()))
        save_lines(self.creator_urls_path, set(self.creator_urls.values()))

    def reset(self) -> None:
        for path in (
            self.visited_sitemaps_path,
            self.discovered_urls_path,
            self.pending_sitemaps_path,
            self.artwork_urls_path,
            self.dealer_urls_path,
            self.creator_urls_path,
        ):
            if path.is_file():
                path.unlink()
        self.visited_sitemap_pages.clear()
        self.discovered_urls.clear()
        self.pending_sitemap_pages.clear()
        self.artwork_urls.clear()
        self.dealer_urls.clear()
        self.creator_urls.clear()

    def add_entity_url(self, kind: str, key: str, url: str) -> bool:
        """Add an entity URL; return True when a new key was inserted."""
        if kind == "item":
            store = self.artwork_urls
        elif kind == "dealer":
            store = self.dealer_urls
        elif kind == "creator":
            store = self.creator_urls
        else:
            return False

        existing = store.get(key)
        if existing is None:
            store[key] = url
            return True
        store[key] = merge_entity_url(existing, url, kind=kind)  # type: ignore[arg-type]
        return False

    def ingest_url(self, url: str) -> bool:
        """Classify and store a URL; return True when a new entity key was added."""
        classified = classify_url(url)
        if classified is None:
            return False
        kind, key, canonical = classified
        return self.add_entity_url(kind, key, canonical)

    def art_sitemap_pages_visited(self) -> int:
        return sum(1 for url in self.visited_sitemap_pages if is_art_sitemap_page(url))

    def total_unique_urls(self) -> int:
        return len(self.artwork_urls) + len(self.dealer_urls) + len(self.creator_urls)

    def all_entity_urls(self) -> set[str]:
        return set(self.artwork_urls.values()) | set(self.dealer_urls.values()) | set(
            self.creator_urls.values()
        )

    def _load_entity_urls(self, path: Path, store: dict[str, str]) -> None:
        for url in load_lines(path):
            classified = classify_url(url)
            if classified is None:
                continue
            kind, key, canonical = classified
            existing = store.get(key)
            store[key] = merge_entity_url(existing, canonical, kind=kind)  # type: ignore[arg-type]

    def _migrate_legacy_discovered_urls(self) -> None:
        if not self.discovered_urls_path.is_file():
            return
        if (
            self.artwork_urls_path.is_file()
            or self.dealer_urls_path.is_file()
            or self.creator_urls_path.is_file()
        ):
            return
        before = self.total_unique_urls()
        migrated = 0
        with self.discovered_urls_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                url = line.strip()
                if not url:
                    continue
                if self.ingest_url(url):
                    migrated += 1
        after = self.total_unique_urls()
        if after > before:
            LOGGER.info(
                "migrated %s entities from legacy discovered_urls.txt (total=%s)",
                migrated,
                after,
            )

    def _seed_art_roots_if_needed(self) -> None:
        if self.pending_sitemap_pages:
            return
        for seed in ART_SITEMAP_SEEDS:
            if seed not in self.visited_sitemap_pages:
                self.pending_sitemap_pages.append(seed)


def write_art_outputs(
    output_dir: Path,
    *,
    artwork_urls: dict[str, str],
    dealer_urls: dict[str, str],
    creator_urls: dict[str, str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "artwork": output_dir / OUTPUT_ARTWORK,
        "dealer": output_dir / OUTPUT_DEALER,
        "creator": output_dir / OUTPUT_CREATOR,
        "all": output_dir / OUTPUT_ALL,
    }
    save_lines(paths["artwork"], set(artwork_urls.values()))
    save_lines(paths["dealer"], set(dealer_urls.values()))
    save_lines(paths["creator"], set(creator_urls.values()))
    all_urls = set(artwork_urls.values()) | set(dealer_urls.values()) | set(creator_urls.values())
    save_lines(paths["all"], all_urls)
    return paths


def write_output(
    output_dir: Path,
    *,
    source: str,
    sitemap_url: str,
    sitemap_pages_crawled: int,
    urls: set[str],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_path = output_dir / OUTPUT_TXT
    json_path = output_dir / OUTPUT_JSON
    sorted_urls = sorted(urls)

    with txt_path.open("w", encoding="utf-8") as handle:
        for url in sorted_urls:
            handle.write(f"{url}\n")

    payload = {
        "source": source,
        "sitemap_url": sitemap_url,
        "sitemap_pages_crawled": sitemap_pages_crawled,
        "total_urls": len(sorted_urls),
        "urls": sorted_urls,
    }
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")

    return txt_path, json_path
