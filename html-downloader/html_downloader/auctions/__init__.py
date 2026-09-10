"""Auction-house crawling (separate from marketplace product crawls)."""

from __future__ import annotations

from html_downloader.auctions.base import AuctionSpec
from html_downloader.auctions.paths import AUCTION_HOUSES
from html_downloader.auctions.registry import AUCTIONS, get_auction

__all__ = ["AUCTION_HOUSES", "AUCTIONS", "AuctionSpec", "get_auction"]