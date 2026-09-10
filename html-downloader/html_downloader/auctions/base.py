"""Auction house specification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuctionSpec:
    """Configuration for one auction data source."""

    name: str
    default_indexes: tuple[str, ...]
    default_concurrency: int
    uses_stealth_proxy: bool
