"""The checkout steps. Each one does ONE thing outside this process: a call to the
inventory, a call to the payment provider, or a write to the database.

A step that fails raises one of these, so the caller knows what went wrong:
  ServiceUnavailable  inventory or the provider is down, slow or answered 5xx. Later may work
  CardDeclined        the bank said no
  OutOfStock          nothing left to reserve
  ShippingRefused     the warehouse won't ship this order
"""

import asyncio
from dataclasses import dataclass

import httpx
from temporalio import activity

import config
import db


@dataclass
class Order:
    order_id: str
    customer_id: str
    sku: str
    quantity: int
    amount_cents: int
    status: str = "PENDING"
    charge_id: str = ""


class ServiceUnavailable(Exception): ...
class CardDeclined(Exception): ...
class OutOfStock(Exception): ...
class ShippingRefused(Exception): ...


@activity.defn
async def save_order(order: Order) -> None:
    await db.query(
        """INSERT INTO checkout.orders (order_id, customer_id, sku, quantity, amount_cents, status, charge_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (order_id) DO UPDATE SET status = excluded.status, charge_id = excluded.charge_id, updated_at = now()""",
        order.order_id, order.customer_id, order.sku, order.quantity, order.amount_cents, order.status, order.charge_id,
    )


@activity.defn
async def reserve_stock(order: Order) -> None:
    body = {"order_id": order.order_id, "sku": order.sku, "quantity": order.quantity}
    response = await call("POST", f"{config.INVENTORY_URL}/reservations", json=body)
    if response.status_code == 409:
        raise OutOfStock(response.json()["detail"])


@activity.defn
async def charge_card(order: Order) -> str:
    """Charge the customer's saved card. Returns the provider's charge id."""
    customer = await db.query("SELECT payment_method_id FROM checkout.customers WHERE customer_id = %s", order.customer_id)
    body = {"amount": order.amount_cents, "payment_method": customer["payment_method_id"], "order_id": order.order_id}
    # The same order always sends the same key: the provider never charges it twice.
    headers = {"Idempotency-Key": f"{order.order_id}:charge"}
    response = await call("POST", f"{config.PAYMENT_PROVIDER_URL}/v1/charges", json=body, headers=headers)
    if response.status_code == 402:
        raise CardDeclined("card declined")
    return response.json()["id"]


@activity.defn
async def ship_order(order: Order) -> None:
    response = await call("POST", f"{config.INVENTORY_URL}/reservations/{order.order_id}/ship")
    if response.status_code == 422:
        raise ShippingRefused(response.json()["detail"])


# --- undoing a step ---------------------------------------------------------


@activity.defn
async def release_stock(order: Order) -> None:
    await call("POST", f"{config.INVENTORY_URL}/reservations/{order.order_id}/release")


@activity.defn
async def refund_charge(order: Order) -> None:
    headers = {"Idempotency-Key": f"{order.order_id}:refund"}
    await call("POST", f"{config.PAYMENT_PROVIDER_URL}/v1/refunds", json={"charge": order.charge_id}, headers=headers)


async def call(method: str, url: str, **kwargs) -> httpx.Response:
    """One HTTP request. A down, slow or failing service becomes ServiceUnavailable."""
    try:
        async with httpx.AsyncClient(timeout=config.CALL_TIMEOUT_S) as http:
            response = await http.request(method, url, **kwargs)
    except httpx.HTTPError as err:
        raise ServiceUnavailable(f"{url}: {type(err).__name__}") from None
    if response.status_code >= 500:
        raise ServiceUnavailable(f"{url}: {response.json().get('detail', response.status_code)}")
    await asyncio.sleep(config.STEP_DELAY_S)
    return response
