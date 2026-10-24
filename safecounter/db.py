"""Database access as the restricted app role (sc_app).

Every shop-scoped unit of work runs in `shop_tx`, which sets app.shop_id with
SET LOCAL semantics (set_config(..., true)) so row-level security applies and the
setting can never leak to the next user of a pooled connection.
"""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.APP_DSN, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def shop_tx(shop_id: str):
    """One transaction, scoped to one shop. Commits on success, rolls back on any error."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.shop_id', %s, true)", (shop_id,))
                # Bound how long we wait for a row lock (e.g. a concurrent approval).
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '10s'")
                yield cur


@contextmanager
def plain_tx():
    """A transaction with no shop set: only the directory tables are visible."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur
