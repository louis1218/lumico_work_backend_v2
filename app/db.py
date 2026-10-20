"""One small shared connection pool (replaces the Client created in every Lambda)."""
import threading
from typing import Any, Optional, Sequence

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: Optional[ConnectionPool] = None
_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                # Small on purpose: the DB is a db.t4g.micro (1 GB RAM).
                pool = ConnectionPool(
                    kwargs={**config.conninfo(), "row_factory": dict_row},
                    min_size=1,
                    max_size=3,
                    open=False,
                )
                pool.open(wait=True, timeout=15)
                _pool = pool
    return _pool


def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Run a write with no RETURNING and give back the affected row count."""
    with get_pool().connection() as conn:
        return conn.execute(sql, params).rowcount


def run(sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
    """Run a write that has RETURNING and give back the first row (committed on exit)."""
    return fetch_one(sql, params)
