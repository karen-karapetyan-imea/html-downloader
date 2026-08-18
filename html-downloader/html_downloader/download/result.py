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
