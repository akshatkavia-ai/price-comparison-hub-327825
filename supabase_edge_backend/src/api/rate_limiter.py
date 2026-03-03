import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limiter configuration."""
    requests: int
    window_seconds: int


class InMemoryRateLimiter:
    """
    Very small in-memory sliding-window rate limiter.

    NOTE: This is process-local and not suitable for multi-replica production by itself.
    For Supabase Edge Functions (Deno) you'd typically use KV/Redis or DB-backed limiting.
    """

    def __init__(self) -> None:
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    def allow(self, key: str, scope: str, config: RateLimitConfig) -> bool:
        now = time.time()
        q = self._hits[(key, scope)]

        # Evict old timestamps
        cutoff = now - config.window_seconds
        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= config.requests:
            return False

        q.append(now)
        return True
