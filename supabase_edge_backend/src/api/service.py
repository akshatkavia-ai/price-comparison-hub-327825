from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from src.api.cache import CacheConfig, InMemoryTTLCache
from src.api.models import ComparePricesRequest, ComparePricesResponse, PriceOffer
from src.api.scraper import scrape_all_sites

logger = logging.getLogger("price-pal.service")


class Persistence(Protocol):
    """Persistence protocol for caching and history.

    Implementations:
      - PostgresPersistence (durable)
      - InMemoryHistoryStore/InMemoryTTLCache (legacy, used only if DB not configured)
    """

    def cache_get_compare(self, query: str) -> Optional[ComparePricesResponse]: ...
    def cache_set_compare(self, query: str, resp: ComparePricesResponse) -> None: ...
    def append_history_snapshots(self, query: str, offers: List[PriceOffer], job_id: Optional[str] = None) -> None: ...
    def price_history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]: ...


@dataclass
class HistoryItem:
    ts: datetime
    offer: PriceOffer


class InMemoryHistoryStore:
    """In-memory history store. Replace with Supabase/Postgres for production."""

    def __init__(self) -> None:
        from threading import RLock

        self._lock = RLock()
        self._by_query: Dict[str, List[HistoryItem]] = {}

    def append(self, query: str, offers: List[PriceOffer]) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            arr = self._by_query.setdefault(query, [])
            for offer in offers:
                arr.append(HistoryItem(ts=now, offer=offer))

    def get(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._by_query.get(query, []))
        items = items[-limit:]
        return [
            {
                "ts": it.ts.isoformat(),
                "site": it.offer.site,
                "title": it.offer.title,
                "price_inr": it.offer.price_inr,
                "url": str(it.offer.url),
            }
            for it in items
        ]


class InMemoryPersistence:
    """Bridges existing in-memory TTL cache + in-memory history to the Persistence protocol."""

    def __init__(self, cache: InMemoryTTLCache, cache_config: CacheConfig, history: InMemoryHistoryStore) -> None:
        self._cache = cache
        self._cache_config = cache_config
        self._history = history

    # PUBLIC_INTERFACE
    def cache_get_compare(self, query: str) -> Optional[ComparePricesResponse]:
        """Get cached ComparePricesResponse if present."""
        import hashlib

        key = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        if isinstance(cached, ComparePricesResponse):
            return cached
        return None

    # PUBLIC_INTERFACE
    def cache_set_compare(self, query: str, resp: ComparePricesResponse) -> None:
        """Set cached ComparePricesResponse with TTL."""
        import hashlib

        key = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
        self._cache.set(key, resp, ttl_seconds=self._cache_config.ttl_seconds)

    # PUBLIC_INTERFACE
    def append_history_snapshots(self, query: str, offers: List[PriceOffer], job_id: Optional[str] = None) -> None:
        """Append to in-memory history store."""
        self._history.append(query, offers)

    # PUBLIC_INTERFACE
    def price_history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Get in-memory history items."""
        return self._history.get(query=query, limit=limit)


class PriceComparisonService:
    """Coordinates cache, scraping, and history persistence."""

    def __init__(self, persistence: Persistence) -> None:
        self._persistence = persistence

    async def compare(self, req: ComparePricesRequest) -> ComparePricesResponse:
        if req.use_cache:
            cached = self._persistence.cache_get_compare(req.query)
            if cached:
                # Keep contract: cached=true, generated_at refreshed
                return ComparePricesResponse(
                    query=cached.query,
                    offers=cached.offers,
                    best_offer=cached.best_offer,
                    cached=True,
                    generated_at=datetime.now(timezone.utc),
                )

        offers = await scrape_all_sites(req.query, req.max_results_per_site)

        best: Optional[PriceOffer] = None
        priced = [o for o in offers if isinstance(o.price_inr, (int, float))]
        if priced:
            best = sorted(priced, key=lambda o: float(o.price_inr or 10**18))[0]

        resp = ComparePricesResponse(
            query=req.query,
            offers=offers,
            best_offer=best,
            cached=False,
            generated_at=datetime.now(timezone.utc),
        )

        self._persistence.cache_set_compare(req.query, resp)
        self._persistence.append_history_snapshots(req.query, offers, job_id=None)
        return resp

    def history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self._persistence.price_history(query=query, limit=limit)
