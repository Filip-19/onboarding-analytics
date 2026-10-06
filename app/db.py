import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_URL = "postgresql://localhost/onboarding"

_pool: ConnectionPool | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url(),
            min_size=1,
            max_size=5,
            # UTC everywhere, so "a day" means the same thing in SQL and in the browser.
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_conn():
    with get_pool().connection() as conn:
        yield conn
