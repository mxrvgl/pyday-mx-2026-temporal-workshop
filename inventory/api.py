"""The warehouse: products, stock, and what's held for which order. It stands in for a
service another team owns: you call it, you don't change it.

  POST /reservations                   hold stock for an order (409 if there isn't enough)
  POST /reservations/{order_id}/release  give it back
  POST /reservations/{order_id}/ship     hand it to the carrier

Every call is idempotent per order: asking twice changes nothing the second time.
POST /faults makes it misbehave on purpose: `make inventory-down`, `make inventory-refuse`...
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

log = logging.getLogger("uvicorn.error")
pool = AsyncConnectionPool(os.environ["DATABASE_URL"], open=False, kwargs={"row_factory": dict_row})
faults = {"down": False, "latency_s": 0.0, "fail_next": 0, "refuse_shipping": False}


@asynccontextmanager
async def lifespan(app):
    async with pool:
        yield


app = FastAPI(title="Inventory (mock)", lifespan=lifespan)


async def misbehave() -> None:
    if faults["latency_s"]:
        await asyncio.sleep(faults["latency_s"])
    if faults["down"]:
        raise HTTPException(503, "inventory is down")
    if faults["fail_next"] > 0:
        faults["fail_next"] -= 1
        raise HTTPException(503, "inventory hiccup")


class Reservation(BaseModel):
    order_id: str
    sku: str
    quantity: int = 1


async def find(conn, order_id: str) -> dict:
    cur = await conn.execute("SELECT * FROM inventory.reservations WHERE order_id = %s FOR UPDATE", [order_id])
    if (row := await cur.fetchone()) is None:
        raise HTTPException(404, f"nothing reserved for {order_id}")
    return row


@app.post("/reservations", dependencies=[Depends(misbehave)])
async def reserve(req: Reservation):
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT * FROM inventory.reservations WHERE order_id = %s", [req.order_id])
        if existing := await cur.fetchone():
            return existing  # asked before: same answer
        cur = await conn.execute(
            "UPDATE inventory.products SET stock = stock - %s WHERE sku = %s AND stock >= %s RETURNING stock",
            [req.quantity, req.sku, req.quantity],
        )
        if await cur.fetchone() is None:
            raise HTTPException(409, f"not enough {req.sku} in stock")
        cur = await conn.execute(
            "INSERT INTO inventory.reservations (order_id, sku, quantity, status) VALUES (%s, %s, %s, 'RESERVED') RETURNING *",
            [req.order_id, req.sku, req.quantity],
        )
        log.info("reserve   %s  %d x %s", req.order_id, req.quantity, req.sku)
        return await cur.fetchone()


@app.post("/reservations/{order_id}/release", dependencies=[Depends(misbehave)])
async def release(order_id: str):
    async with pool.connection() as conn, conn.transaction():
        reservation = await find(conn, order_id)
        if reservation["status"] == "SHIPPED":
            raise HTTPException(409, f"{order_id} has already shipped")
        if reservation["status"] == "RESERVED":
            await conn.execute(
                "UPDATE inventory.products SET stock = stock + %s WHERE sku = %s", [reservation["quantity"], reservation["sku"]]
            )
            await conn.execute(
                "UPDATE inventory.reservations SET status = 'RELEASED', updated_at = now() WHERE order_id = %s", [order_id]
            )
            log.info("release   %s", order_id)
        return {**reservation, "status": "RELEASED"}


@app.post("/reservations/{order_id}/ship", dependencies=[Depends(misbehave)])
async def ship(order_id: str):
    async with pool.connection() as conn, conn.transaction():
        reservation = await find(conn, order_id)
        if reservation["status"] == "SHIPPED":
            return reservation
        if reservation["status"] != "RESERVED":
            raise HTTPException(409, f"{order_id} is {reservation['status']}")
        if faults["refuse_shipping"]:
            log.info("ship      %s  refused", order_id)
            raise HTTPException(422, "the warehouse refuses to ship this order")
        await conn.execute(
            "UPDATE inventory.reservations SET status = 'SHIPPED', updated_at = now() WHERE order_id = %s", [order_id]
        )
        log.info("ship      %s", order_id)
        return {**reservation, "status": "SHIPPED"}


@app.get("/products")
async def products():
    async with pool.connection() as conn:
        return await (await conn.execute("SELECT * FROM inventory.products ORDER BY sku")).fetchall()


@app.get("/products/{sku}")
async def product(sku: str):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM inventory.products WHERE sku = %s", [sku])
        if (row := await cur.fetchone()) is None:
            raise HTTPException(404, f"no such product {sku}")
        return row


@app.get("/reservations")
async def reservations():
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM inventory.reservations ORDER BY updated_at DESC LIMIT 20")
        return await cur.fetchall()


@app.get("/reservations/{order_id}")
async def reservation(order_id: str):
    async with pool.connection() as conn:
        return await find(conn, order_id)


@app.get("/faults")
async def get_faults():
    return faults


@app.post("/faults")
async def set_faults(down: bool = False, latency_s: float = 0, fail_next: int = 0, refuse_shipping: bool = False):
    faults.update(down=down, latency_s=latency_s, fail_next=fail_next, refuse_shipping=refuse_shipping)
    log.info("faults    %s", faults)
    return faults


@app.get("/health")
async def health():
    return {"ok": True}
