"""
Postgres adapter for the FastAPI scaffold.

This module intentionally isolates DB connectivity and query execution.
In the intended Supabase Edge (Deno) future, this will be replaced by Supabase client calls,
but keeping the interface here stable helps preserve API contracts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras


@dataclass(frozen=True)
class PostgresConfig:
    """Configuration required to connect to Postgres.

    Uses environment variables consistent with the database container env vars.
    """

    url: str
    user: str
    password: str
    db: str
    port: str
    host: str


def _parse_postgres_url(url: str) -> Dict[str, str]:
    """
    Parse a postgres URL in the common format postgresql://user:pass@host:port/db.

    We do this only to allow either POSTGRES_URL *or* explicit POSTGRES_* pieces.
    This is best-effort and intentionally minimal.
    """
    # Very small parser to avoid adding new deps.
    # psycopg2 can accept a DSN string, but we also want env parity with db container vars.
    if not url.startswith("postgresql://") and not url.startswith("postgres://"):
        return {}
    # Remove scheme
    rest = url.split("://", 1)[1]
    creds, hostpart = rest.split("@", 1)
    user, pwd = creds.split(":", 1)
    host_port, db = hostpart.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
    else:
        host, port = host_port, "5432"
    return {"user": user, "password": pwd, "host": host, "port": port, "db": db}


# PUBLIC_INTERFACE
def load_postgres_config() -> Optional[PostgresConfig]:
    """Load Postgres configuration from environment variables.

    Contract:
        Inputs:
            Reads env vars:
              - POSTGRES_URL (optional but recommended)
              - POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT (optional if POSTGRES_URL given)
        Output:
            PostgresConfig if enough information exists, otherwise None.
        Errors:
            Never raises; returns None if configuration is incomplete.
        Side effects:
            None.
    """
    url = (os.getenv("POSTGRES_URL") or "").strip()
    user = (os.getenv("POSTGRES_USER") or "").strip()
    password = (os.getenv("POSTGRES_PASSWORD") or "").strip()
    db = (os.getenv("POSTGRES_DB") or "").strip()
    port = (os.getenv("POSTGRES_PORT") or "").strip()

    host = ""
    if url:
        parsed = _parse_postgres_url(url)
        host = parsed.get("host", "")
        user = user or parsed.get("user", "")
        password = password or parsed.get("password", "")
        db = db or parsed.get("db", "")
        port = port or parsed.get("port", "")

    if not url and (not host or not db):
        # If URL not given, we still might be able to connect using discrete vars,
        # but we don't have a separate POSTGRES_HOST in the db container contract.
        # So: require POSTGRES_URL for this scaffold.
        return None

    if not port:
        port = "5432"

    if not user or not password or not db:
        return None

    # host is optional when using url DSN, but keep for completeness
    if not host:
        parsed = _parse_postgres_url(url)
        host = parsed.get("host", "")

    return PostgresConfig(url=url, user=user, password=password, db=db, port=port, host=host)


class PostgresClient:
    """Very small psycopg2 wrapper with JSON helpers.

    IMPORTANT: uses short-lived connections per operation to keep things simple in this scaffold.
    """

    def __init__(self, cfg: PostgresConfig, schema: str = "pricepal") -> None:
        self._cfg = cfg
        self._schema = schema

    def _connect(self):
        # Use URL DSN if present; psycopg2 supports it.
        conn = psycopg2.connect(self._cfg.url)
        conn.autocommit = True
        return conn

    def execute_one(self, sql: str, params: tuple = ()) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self._schema}, public;")
                cur.execute(sql, params)

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"SET search_path TO {self._schema}, public;")
                cur.execute(sql, params)
                row = cur.fetchone()
                return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"SET search_path TO {self._schema}, public;")
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
