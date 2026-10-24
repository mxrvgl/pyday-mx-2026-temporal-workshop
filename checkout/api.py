"""The checkout API: POST /orders to buy, GET /orders/{id} to look an order up."""

import logging
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
import db
from checkout import place_order
from steps import Order, save_order

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
app = FastAPI(title="Checkout")


class NewOrder(BaseModel):
    customer_id: str = "alice"
    sku: str = "TSHIRT-M"
    quantity: int = 1
    order_id: str = ""


@app.post("/orders")
async def create_order(new: NewOrder) -> dict:
    order = Order(
        order_id=new.order_id or f"order-{uuid.uuid4().hex[:6]}",
        customer_id=new.customer_id,
        sku=new.sku,
        quantity=new.quantity,
        amount_cents=await price_of(new.sku) * new.quantity,
    )
    try:
        status = await place_order(order)
    except Exception as err:
        order.status = "FAILED"
        await save_order(order)
        status = f"FAILED: {type(err).__name__}: {err}"
    return {"order_id": order.order_id, "status": status}


@app.get("/orders")
async def list_orders() -> list[dict]:
    """The latest orders, newest first. `make board` uses this."""
    return await db.query_all("SELECT * FROM checkout.orders ORDER BY created_at DESC LIMIT 15")


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    order = await db.query("SELECT * FROM checkout.orders WHERE order_id = %s", order_id)
    if order is None:
        raise HTTPException(404, f"no order {order_id}")
    return order


async def price_of(sku: str) -> int:
    async with httpx.AsyncClient() as http:
        response = await http.get(f"{config.INVENTORY_URL}/products/{sku}")
    if response.status_code != 200:
        raise HTTPException(400, f"unknown product {sku}")
    return response.json()["price_cents"]
