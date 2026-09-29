"""Per-partition (sale / auction) checkpoint shared by API-driven auction discovery."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


class SaleSearchState:
    """Per-sale checkpoint (``done`` / ``failed`` / house-specific) with batched atomic saves."""

    _SAVE_EVERY = 100

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._dirty = 0
        self._sales: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("sales"), dict):
                    self._sales = loaded["sales"]
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("could not load search state %s: %s", path, exc)

    def status(self, key: str) -> str | None:
        entry = self._sales.get(key)
        return entry.get("status") if entry else None

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._sales.get(key)
            return dict(entry) if entry else None

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [(key, dict(entry)) for key, entry in self._sales.items()]

    def keys_with_status(self, *statuses: str) -> list[str]:
        """Keys whose status is one of ``statuses``, in insertion order."""
        wanted = set(statuses)
        with self._lock:
            return [key for key, entry in self._sales.items() if entry.get("status") in wanted]

    def mark(
        self, key: str, status: str, *, hits: int = 0, written: int = 0, **extra: Any
    ) -> None:
        """Replace ``key``'s entry; ``extra`` holds optional audit fields."""
        with self._lock:
            self._sales[key] = {"status": status, "hits": hits, "written": written, **extra}
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset(self) -> None:
        with self._lock:
            self._sales = {}
            self._save_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._save_unlocked()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._sales.values():
            status = str(entry.get("status"))
            out[status] = out.get(status, 0) + 1
        return out

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"sales": self._sales}, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0


__all__ = ["SaleSearchState"]
