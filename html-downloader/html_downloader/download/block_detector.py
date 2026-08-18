"""
Block detection: status 403/429/503, cf-mitigated header, body keywords.
Returns block reason for logging and adaptive backoff.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class BlockInfo:
    """Result of block detection."""

    is_block: bool
    reason: str


def is_block(
    status_code: int,
    headers: Any,
    body: bytes | None,
    *,
    block_status_codes: tuple[int, ...] = (403, 429, 503),
    scan_bytes: int = 16384,
    keywords: tuple[str, ...] = (
        "cloudflare",
        "access denied",
        "blocked",
        "captcha",
        "challenge",
        "rate limit",
        "ddos",
        "akamai",
    ),
) -> BlockInfo:
    """
    Detect soft block from status, headers, and body.
    Scans first scan_bytes of body for keywords (case-insensitive).
    """
    if status_code in block_status_codes:
        return BlockInfo(True, f"status_{status_code}")

    if headers:
        cf = headers.get("cf-mitigated") or headers.get("Cf-Mitigated")
        if cf and str(cf).strip().lower() == "challenge":
            return BlockInfo(True, "cf_challenge")

    if body and len(body) > 0:
        sample = body[:scan_bytes].decode("utf-8", errors="ignore").lower()
        for kw in keywords:
            if kw in sample:
                return BlockInfo(True, f"body_{kw.replace(' ', '_')}")

    return BlockInfo(False, "")
