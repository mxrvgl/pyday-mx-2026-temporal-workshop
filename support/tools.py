"""The agent's tools. Each one reads the shop database, asks the payment provider,
or both. They get the order ID from the conversation, never from the model: the
agent can only see, and refund, the order the customer opened the chat about.

The refund policy is code, not a prompt, and issue_refund checks it again itself,
so no conversation can talk the agent past it.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

import config
import db


class ServiceUnavailable(Exception): ...


async def lookup_order(order_id: str) -> dict:
    """The order, and whether the payment provider charged or refunded it."""
    order = await db.query(
        "SELECT order_id, customer_id, sku, quantity, amount_cents, status, charge_id, created_at"
        " FROM checkout.orders WHERE order_id = %s",
        order_id,
    )
    if order is None:
        return {"found": False}
    ledger = (await call("GET", f"{config.PAYMENT_PROVIDER_URL}/v1/ledger", params={"order_id": order_id})).json()
    done = {e["operation"] for e in ledger if e["result"] == "OK"}
    return {
        "found": True,
        **order,
        "created_at": order["created_at"].isoformat(),
        "amount": f"MXN {order['amount_cents'] / 100:,.2f}",
        "charged": "charge" in done,
        "refunded": "refund" in done,
    }


async def get_purchase_history(order_id: str) -> dict:
    """Everything this order's customer bought, and what was refunded."""
    order = await lookup_order(order_id)
    rows = await db.query_all(
        """SELECT o.order_id, o.sku, o.amount_cents, o.created_at, r.refunded_at
           FROM checkout.orders o LEFT JOIN support.refunds r USING (order_id)
           WHERE o.customer_id = %s ORDER BY o.created_at DESC""",
        order.get("customer_id"),
    )
    purchases = [
        {**row, "created_at": row["created_at"].isoformat(),
         "refunded_at": row["refunded_at"].isoformat() if row["refunded_at"] else None}
        for row in rows
    ]
    return {"customer_id": order.get("customer_id"), "purchases": purchases}


async def check_refund_policy(order_id: str) -> dict:
    """Whether this order can be refunded, and why."""
    order = await lookup_order(order_id)
    history = (await get_purchase_history(order_id))["purchases"]
    return refund_policy(order, history, datetime.now(timezone.utc))


async def issue_refund(order_id: str) -> dict:
    """Refund the order in full, if the policy says so."""
    decision = await check_refund_policy(order_id)
    if decision["verdict"] != "approve":
        return {"refunded": False, **decision}
    order = await lookup_order(order_id)
    # The same key checkout uses to refund: the provider never refunds an order twice.
    headers = {"Idempotency-Key": f"{order_id}:refund"}
    response = await call("POST", f"{config.PAYMENT_PROVIDER_URL}/v1/refunds", json={"charge": order["charge_id"]}, headers=headers)
    await db.query(
        "INSERT INTO support.refunds (order_id, customer_id, amount_cents, refund_id) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (order_id) DO NOTHING",
        order_id, order["customer_id"], order["amount_cents"], response.json()["id"],
    )
    return {"refunded": True, "amount": order["amount"]}


TOOLS_BY_NAME = {f.__name__: f for f in (lookup_order, get_purchase_history, check_refund_policy, issue_refund)}


def refund_policy(order: dict, history: list[dict], now: datetime) -> dict:
    def days_since(timestamp: str) -> float:
        return (now - datetime.fromisoformat(timestamp)) / timedelta(days=1)

    # Refunds in the 90 days before today. Today's don't count, so trying things out never locks you out.
    recent_refunds = [p for p in history if p["refunded_at"] and 1 <= days_since(p["refunded_at"]) < 90]
    if not order["found"] or order["status"] != "COMPLETED":
        return {"verdict": "deny", "reason": "only completed orders can be refunded"}
    if order["refunded"]:
        return {"verdict": "deny", "reason": "this order was already refunded"}
    if days_since(order["created_at"]) > config.REFUND_WINDOW_DAYS:
        return {"verdict": "deny", "reason": f"refunds are only possible within {config.REFUND_WINDOW_DAYS} days"}
    if len(recent_refunds) >= config.MAX_RECENT_REFUNDS:
        return {"verdict": "deny", "reason": "this account has had too many refunds recently"}
    if not order["charged"]:
        return {"verdict": "deny", "reason": "no payment was taken for this order"}
    if order["amount_cents"] > config.REVIEW_ABOVE_CENTS:
        return {"verdict": "review", "reason": "large refunds are approved by a person: we'll email you"}
    return {"verdict": "approve", "reason": "within the refund window"}


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
