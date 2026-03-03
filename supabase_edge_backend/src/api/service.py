from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from src.api.cache import CacheConfig, InMemoryTTLCache
from src.api.models import ComparePricesRequest, ComparePricesResponse, PriceOffer
from src.api.scraper import scrape_all_sites


def _cache_key_for_query(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()


@dataclass
class HistoryItem:
    ts: datetime
    offer: PriceOffer


class InMemoryHistoryStore:
    """In-memory history store. Replace with Supabase/Postgres for production."""

    def __init__(self) -> None:
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


class PriceComparisonService:
    """Coordinates cache, scraping, and history persistence."""

    def __init__(self, cache: InMemoryTTLCache, cache_config: CacheConfig, history: InMemoryHistoryStore) -> None:
        self._cache = cache
        self._cache_config = cache_config
        self._history = history

    async def compare(self, req: ComparePricesRequest) -> ComparePricesResponse:
        key = _cache_key_for_query(req.query)

        if req.use_cache:
            cached = self._cache.get(key)
            if isinstance(cached, ComparePricesResponse):
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

        self._cache.set(key, resp, ttl_seconds=self._cache_config.ttl_seconds)
        self._history.append(req.query, offers)
        return resp

    def history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self._history.get(query=query, limit=limit)
