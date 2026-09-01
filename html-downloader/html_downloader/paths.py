"""Job folder contract: data/{marketplace}/{YYYY-MM-DD}/."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_STATE_ROOT = PROJECT_ROOT / "state"

MARKETPLACES: tuple[str, ...] = ("saatchi", "artsper", "artsy", "artmajeur", "singulart")


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def parse_crawl_date(value: str | None) -> date:
    if value is None:
        return utc_today()
    return date.fromisoformat(value)


def job_dir(data_root: Path, marketplace: str, crawl_date: date) -> Path:
    return data_root / marketplace / crawl_date.isoformat()


def html_dir(job: Path) -> Path:
    return job / "html"


def urls_file(job: Path) -> Path:
    return job / "urls.txt"


def sitemap_all_file(job: Path) -> Path:
    return job / "sitemap_all.txt"


def results_file(job: Path) -> Path:
    return job / "results.jsonl"


def diff_file(job: Path) -> Path:
    return job / "diff.json"


def manifest_file(job: Path) -> Path:
    return job / "manifest.json"


def lastmod_state_file(state_root: Path, marketplace: str) -> Path:
    return state_root / f"{marketplace}_lastmod.json"


def known_result_paths(data_root: Path, marketplace: str) -> list[Path]:
    """Prior crawl logs for incremental discovery."""
    market_dir = data_root / marketplace
    if not market_dir.is_dir():
        return []
    return sorted(path for path in market_dir.glob("*/results.jsonl") if path.is_file())


def ensure_job_dirs(job: Path) -> Path:
    html_dir(job).mkdir(parents=True, exist_ok=True)
    return job
