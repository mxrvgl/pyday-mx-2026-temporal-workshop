"""One or two checks per step. Your workflow runs on Temporal's test server, with
stand-ins for the inventory, the payment provider and the database."""

import asyncio

import httpx
import pytest
from conftest import need
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

import config
import steps
from steps import Order

step = pytest.mark.step
ACTIVITIES = ["save_order", "reserve_stock", "charge_card", "ship_order", "release_stock", "refund_charge"]


def is_activity(fn) -> bool:
    return activity._Definition.from_callable(fn) is not None


def stand_ins(calls: list[str], failures: dict[str, list[Exception]] | None = None) -> list:
    """Activities named like yours: they record each call, and raise what `failures` says."""
    failures = failures or {}

    def stand_in(name: str):
        @activity.defn(name=name)
        async def run(order: Order) -> str | None:
            calls.append(name)
            if failures.get(name):
                raise failures[name].pop(0)
            return "ch_test" if name == "charge_card" else None

        return run

    return [stand_in(name) for name in ACTIVITIES]


async def checkout(env, calls, failures=None, order_id="order-test") -> str:
    CheckoutWorkflow = need("workflows", "CheckoutWorkflow", 2)
    order = Order(order_id, "alice", "TSHIRT-M", 1, 49900)
    async with Worker(env.client, task_queue=config.TASK_QUEUE, workflows=[CheckoutWorkflow],
                      activities=stand_ins(calls, failures)):
        try:
            return await env.client.execute_workflow(
                CheckoutWorkflow.run, order, id=order.order_id, task_queue=config.TASK_QUEUE
            )
        except WorkflowFailureError as err:
            return f"FAILED: {err.cause}"


@step(1)
def test_each_step_is_an_activity():
    for name in ACTIVITIES:
        assert is_activity(getattr(steps, name)), f"add @activity.defn to {name} in steps.py"


@step(2)
async def test_the_workflow_runs_the_steps_in_order(env):
    calls: list[str] = []
    assert await checkout(env, calls) == "COMPLETED"
    assert calls == ["save_order", "reserve_stock", "charge_card", "ship_order", "save_order"]


@step(3)
async def test_the_worker_runs_a_checkout(env, monkeypatch):
    main = need("worker", "main", 3)
    CheckoutWorkflow = need("workflows", "CheckoutWorkflow", 2)

    async def fake_call(method, url, **kwargs):
        return httpx.Response(200, json={"id": "ch_test"})

    async def fake_query(sql, *params):
        return {"payment_method_id": "pm_card_visa"}

    async def test_server(*args, **kwargs):
        return env.client

    monkeypatch.setattr(steps, "call", fake_call)
    monkeypatch.setattr(steps.db, "query", fake_query)
    monkeypatch.setattr(Client, "connect", test_server)
    worker = asyncio.create_task(main())
    try:
        order = Order("order-step3", "alice", "TSHIRT-M", 1, 49900)
        status = await asyncio.wait_for(env.client.execute_workflow(
            CheckoutWorkflow.run, order, id=order.order_id, task_queue=config.TASK_QUEUE
        ), timeout=30)
        assert status == "COMPLETED"
    finally:
        worker.cancel()


@step(4)
async def test_the_api_runs_the_workflow(env, monkeypatch):
    import api

    need("api", "temporal_client", 4)
    CheckoutWorkflow = need("workflows", "CheckoutWorkflow", 2)

    async def test_server():
        return env.client

    async def price_of(sku):
        return 49900

    monkeypatch.setattr(api, "temporal_client", test_server)
    monkeypatch.setattr(api, "price_of", price_of)
    calls: list[str] = []
    async with Worker(env.client, task_queue=config.TASK_QUEUE, workflows=[CheckoutWorkflow],
                      activities=stand_ins(calls)):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://checkout") as http:
            answer = (await http.post("/orders", json={"order_id": "order-step4"})).json()
        assert answer["status"] == "COMPLETED"
        assert "charge_card" in calls, "POST /orders should run the workflow, not place_order()"
        description = await env.client.get_workflow_handle("order-step4").describe()
        assert description.status.name == "COMPLETED", "use the order ID as the workflow ID"


@step(5)
async def test_outages_are_retried_and_declines_are_not(env):
    calls: list[str] = []
    outage = [steps.ServiceUnavailable("payment provider is down")] * 3
    assert await checkout(env, calls, {"charge_card": outage}) == "COMPLETED"
    assert calls.count("charge_card") == 4

    calls.clear()
    give_up = ApplicationError("a declined card was retried", non_retryable=True)
    status = await checkout(env, calls, {"charge_card": [steps.CardDeclined("card declined"), give_up]}, "order-bob")
    assert calls.count("charge_card") == 1, "CardDeclined should be non-retryable"
    assert status != "COMPLETED"


@step(6)
async def test_what_was_done_is_undone(env):
    calls: list[str] = []
    refused = [steps.ShippingRefused("the warehouse refuses to ship this order")]
    assert await checkout(env, calls, {"ship_order": refused}) == "CANCELLED"
    assert calls[-3:] == ["refund_charge", "release_stock", "save_order"], "refund, release, then save"

    calls.clear()
    assert await checkout(env, calls, {"charge_card": [steps.CardDeclined("card declined")]}, "order-bob") == "DECLINED"
    assert "refund_charge" not in calls, "nothing was charged, so there's nothing to refund"
    assert calls[-2:] == ["release_stock", "save_order"]
