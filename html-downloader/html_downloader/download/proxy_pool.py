"""
Thread-safe proxy pool. Format: host:port:username:password per line.
Supports single proxy (file with one line or env CRAWLER_PROXY) or multi-line file.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


def parse_proxy(proxy_string: str) -> dict[str, str]:
    """
    Parse proxy string in format 'host:port:username:password'.
    Returns dict with http and https proxy URLs for curl_cffi.
    """
    parts = proxy_string.strip().split(":")
    if len(parts) != 4:
        raise ValueError("Proxy format must be 'host:port:username:password'")
    host, port, username, password = parts
    url = f"http://{username}:{password}@{host}:{port}"
    return {"http": url, "https": url}


def load_proxy_list(proxy_file: str | Path | None, env_key: str = "CRAWLER_PROXY") -> list[dict[str, str]]:
    """
    Load proxy list from file or env. File: one host:port:user:pass per line.
    Returns list of proxy dicts; empty if no proxy configured.
    """
    single = os.environ.get(env_key)
    if single and single.strip():
        return [parse_proxy(single)]

    if not proxy_file:
        return []

    path = Path(proxy_file)
    if not path.exists():
        return []

    proxies: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                proxies.append(parse_proxy(line))
            except ValueError:
                continue
    return proxies


class ProxyPool:
    """Thread-safe round-robin proxy pool."""

    def __init__(self, proxy_list: list[dict[str, str]]) -> None:
        self._proxies = proxy_list
        self._index = 0
        self._lock = threading.Lock()

    @classmethod
    def from_file_or_env(
        cls,
        proxy_file: str | Path | None = None,
        env_key: str = "CRAWLER_PROXY",
    ) -> ProxyPool:
        """Build pool from config file path or env. Returns empty pool if none configured."""
        proxies = load_proxy_list(proxy_file, env_key=env_key)
        return cls(proxies)

    def get_next(self) -> dict[str, str] | None:
        """Return next proxy dict for curl_cffi, or None if no proxies."""
        if not self._proxies:
            return None
        with self._lock:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy

    def __len__(self) -> int:
        return len(self._proxies)
