from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.api.db import PostgresClient
from src.api.models import ComparePricesResponse, PriceOffer

logger = logging.getLogger("price-pal.persistence")


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _cache_key_for_query(query: str) -> str:
    return hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PersistenceConfig:
    cache_ttl_seconds: int


class PostgresPersistence:
    """Persistence adapter targeting the `pricepal` schema.

    This is the canonical reusable flow for persistence (Flows, not patches):
      - cache_get_compare/cache_set_compare
      - append_history_snapshots
      - price_history lookup
    """

    def __init__(self, pg: PostgresClient, cfg: PersistenceConfig) -> None:
        self._pg = pg
        self._cfg = cfg

    # PUBLIC_INTERFACE
    def cache_get_compare(self, query: str) -> Optional[ComparePricesResponse]:
        """Fetch cached compare response if present and unexpired.

        Errors:
            Returns None on miss; raises only on DB connectivity/query errors.
        """
        cache_key = _cache_key_for_query(query)
        row = self._pg.fetch_one(
            """
            SELECT payload, expires_at
            FROM cache_entries
            WHERE kind = %s AND cache_key = %s
            LIMIT 1
            """,
            ("compare", cache_key),
        )
        if not row:
            return None

        expires_at = row.get("expires_at")
        if isinstance(expires_at, datetime):
            if expires_at <= datetime.now(timezone.utc):
                return None

        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None

        # Payload stored as dict; re-validate through pydantic model
        try:
            return ComparePricesResponse.model_validate(payload)
        except Exception:
            return None

    # PUBLIC_INTERFACE
    def cache_set_compare(self, query: str, resp: ComparePricesResponse) -> None:
        """Upsert cached compare response with TTL."""
        cache_key = _cache_key_for_query(query)
        normalized_query = _normalize_query(query)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._cfg.cache_ttl_seconds)

        # Upsert on (kind, cache_key)
        self._pg.execute_one(
            """
            INSERT INTO cache_entries (kind, cache_key, normalized_query, ttl_seconds, expires_at, payload, hit_count, last_accessed_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, 0, %s)
            ON CONFLICT (kind, cache_key)
            DO UPDATE SET
              normalized_query = EXCLUDED.normalized_query,
              ttl_seconds = EXCLUDED.ttl_seconds,
              expires_at = EXCLUDED.expires_at,
              payload = EXCLUDED.payload,
              last_accessed_at = EXCLUDED.last_accessed_at
            """,
            (
                "compare",
                cache_key,
                normalized_query,
                int(self._cfg.cache_ttl_seconds),
                expires_at,
                self._pg.dumps_json(resp.model_dump(mode="json")),
                now,
            ),
        )

    # PUBLIC_INTERFACE
    def append_history_snapshots(self, query: str, offers: List[PriceOffer], job_id: Optional[str] = None) -> None:
        """Append offer snapshots into `price_history` (priced offers only).

        Invariant:
            price_history.price_amount is NOT NULL, so we only insert offers that have a numeric price.
        """
        normalized_query = _normalize_query(query)
        now = datetime.now(timezone.utc)

        for o in offers:
            if not isinstance(o.price_inr, (int, float)):
                continue
            self._pg.execute_one(
                """
                INSERT INTO price_history (
                  job_id, normalized_query, retailer, product_title, product_url,
                  currency_code, price_amount, scraped_at
                )
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    job_id,
                    normalized_query,
                    o.site,
                    o.title,
                    str(o.url),
                    o.currency,
                    float(o.price_inr),
                    now,
                ),
            )

    # PUBLIC_INTERFACE
    def price_history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetch recent price history snapshots for a normalized query."""
        normalized_query = _normalize_query(query)
        rows = self._pg.fetch_all(
            """
            SELECT scraped_at, retailer, product_title, price_amount, product_url, currency_code
            FROM price_history
            WHERE normalized_query = %s
            ORDER BY scraped_at DESC
            LIMIT %s
            """,
            (normalized_query, int(limit)),
        )
        # Map to the existing API contract for /price-history
        items: List[Dict[str, Any]] = []
        for r in rows:
            ts = r.get("scraped_at")
            items.append(
                {
                    "ts": ts.isoformat() if isinstance(ts, datetime) else str(ts),
                    "site": r.get("retailer"),
                    "title": r.get("product_title"),
                    "price_inr": float(r["price_amount"]) if r.get("price_amount") is not None else None,
                    "url": r.get("product_url"),
                    "currency": r.get("currency_code") or "INR",
                }
            )
        return items


class NoopPersistence:
    """Fallback persistence used when DB is not configured."""

    # PUBLIC_INTERFACE
    def cache_get_compare(self, query: str) -> Optional[ComparePricesResponse]:
        """Always miss cache when persistence is disabled."""
        return None

    # PUBLIC_INTERFACE
    def cache_set_compare(self, query: str, resp: ComparePricesResponse) -> None:
        """No-op."""
        return None

    # PUBLIC_INTERFACE
    def append_history_snapshots(self, query: str, offers: List[PriceOffer], job_id: Optional[str] = None) -> None:
        """No-op."""
        return None

    # PUBLIC_INTERFACE
    def price_history(self, query: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Return empty history when persistence is disabled."""
        return []
