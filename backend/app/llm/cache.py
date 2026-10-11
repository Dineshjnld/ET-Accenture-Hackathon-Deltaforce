"""Repeatable-request cache (in-memory TTL). Never caches errors or secrets."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


def cache_key(*parts: str) -> str:
    raw = "\u0001".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 500) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            now = time.time()
            if entry is None or now - entry[0] > self.ttl:
                if entry is not None:
                    del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return entry[1]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self.max_entries:
                oldest = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest]
            self._store[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


_cache: TTLCache | None = None
_lock = threading.Lock()


def get_cache(ttl_seconds: int | None = None) -> TTLCache:
    global _cache
    with _lock:
        if _cache is None:
            from app.config import settings

            _cache = TTLCache(ttl_seconds=ttl_seconds or settings.LLM_CACHE_TTL_SECONDS)
        return _cache


def reset_cache() -> TTLCache:
    global _cache
    with _lock:
        from app.config import settings

        _cache = TTLCache(ttl_seconds=settings.LLM_CACHE_TTL_SECONDS)
        return _cache


def safe_key_payload(payload: Any) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(payload)[:2000]


__all__ = ["TTLCache", "cache_key", "get_cache", "reset_cache", "safe_key_payload"]
