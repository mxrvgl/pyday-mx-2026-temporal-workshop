"""Checkout's own tables (checkout.orders, checkout.customers). Nothing to change here."""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

import config

_pool: AsyncConnectionPool | None = None


async def _connection():
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(config.DATABASE_URL, open=False, kwargs={"row_factory": dict_row})
        await _pool.open()
    return _pool.connection()


async def query(sql: str, *params) -> dict | None:
    """Run one statement; return the first row, if any."""
    async with await _connection() as conn:
        cursor = await conn.execute(sql, params)
        return await cursor.fetchone() if cursor.description else None


async def query_all(sql: str, *params) -> list[dict]:
    """Run one statement; return every row."""
    async with await _connection() as conn:
        return await (await conn.execute(sql, params)).fetchall()
