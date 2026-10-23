"""Result dataclass for crawl outcomes."""

from dataclasses import dataclass


@dataclass
class Result:
    """Single URL fetch result."""

    url: str
    filename: str
    status_code: int
    error: str
    block_detected: bool = False
    block_reason: str = ""
    duration_ms: int = 0
    timestamp: str = ""
    provider: str = ""
    api_status_code: int | None = None
    error_category: str = ""
    retry_count: int = 0
    html_bytes: int = 0
    validated: bool = False
    billable_attempts: int = 0
    traffic_bytes: int = 0
    skipped_existing: bool = False
