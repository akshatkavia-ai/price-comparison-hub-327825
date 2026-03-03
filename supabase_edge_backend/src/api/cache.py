import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class CacheConfig:
    ttl_seconds: int = 300


class InMemoryTTLCache:
    """A tiny thread-safe TTL cache for FastAPI single-process usage."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires_at, value = item
            if time.time() >= expires_at:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl_seconds, value)
