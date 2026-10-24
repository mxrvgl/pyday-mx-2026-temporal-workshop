"""One or two checks per step. Your workflow runs on Temporal's test server, with
stand-ins for Claude, the tools, the payment provider and the database."""

import asyncio

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock
from conftest import need
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

import agent
import config
import tools

step = pytest.mark.step
TOOLS = ["lookup_order", "get_purchase_history", "check_refund_policy", "issue_refund"]


def is_activity(fn) -> bool:
    return activity._Definition.from_callable(fn) is not None


def stand_ins(seen: list[int], calls: list[str], failures: int = 0) -> list:
    """A scripted Claude and tools named like yours. For each message, Claude looks
    the order up, then answers. `seen` records how much conversation Claude was sent."""
    failures_left = [failures]

    @activity.defn(name="call_claude")
    async def call_claude(messages: list[dict]) -> list[dict]:
        if failures_left[0]:
            failures_left[0] -= 1
            raise RuntimeError("Claude is overloaded")
        seen.append(len(messages))
        if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], str):
            return [{"type": "tool_use", "id": f"toolu_{len(messages)}", "name": "lookup_order", "input": {}}]
        return [{"type": "text", "text": "Your order is on its way."}]

    def tool(name: str):
        @activity.defn(name=name)
        async def run(order_id: str) -> dict:
            calls.append(name)
            return {"found": True, "order_id": order_id}

        return run

    return [call_claude, *[tool(name) for name in TOOLS]]


@step(7)
def test_claude_and_the_tools_are_activities():
    assert is_activity(agent.call_claude), "add @activity.defn to call_claude in agent.py"
    for name in TOOLS:
        assert is_activity(getattr(tools, name)), f"add @activity.defn to {name} in tools.py"


@step(8)
async def test_the_conversation_is_a_workflow(env):
    SupportWorkflow = need("workflows", "SupportWorkflow", 8)
    assert hasattr(SupportWorkflow, "send_message"), "SupportWorkflow needs a send_message update"
    seen, calls = [], []
    async with Worker(env.client, task_queue=config.TASK_QUEUE, workflows=[SupportWorkflow],
                      activities=stand_ins(seen, calls, failures=2)):
        handle = await env.client.start_workflow(
            SupportWorkflow.run, "order-test", id="support-order-test", task_queue=config.TASK_QUEUE
        )
        first = await handle.execute_update(SupportWorkflow.send_message, "where is my order?")
        assert first == [{"tool": "lookup_order"}, {"agent": "Your order is on its way."}]
        assert calls == ["lookup_order"]
        await handle.execute_update(SupportWorkflow.send_message, "thanks!")
        assert seen == [1, 3, 5, 7], "the second message should be sent to Claude with the whole conversation"


@step(9)
async def test_the_worker_runs_a_conversation(env, monkeypatch):
    main = need("worker", "main", 9)
    SupportWorkflow = need("workflows", "SupportWorkflow", 8)

    class FakeClaude:  # answers without calling the real API
        def __init__(self, **kwargs):
            self.messages = self

        async def create(self, **kwargs):
            return Message.model_construct(content=[TextBlock(type="text", text="Hello!")], stop_reason="end_turn")

    async def test_server(*args, **kwargs):
        return env.client

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClaude)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(Client, "connect", test_server)
    worker = asyncio.create_task(main())
    try:
        handle = await env.client.start_workflow(
            SupportWorkflow.run, "order-step9", id="support-order-step9", task_queue=config.TASK_QUEUE
        )
        replies = await asyncio.wait_for(handle.execute_update(SupportWorkflow.send_message, "hi"), timeout=30)
        assert replies == [{"agent": "Hello!"}]
    finally:
        worker.cancel()


@step(10)
async def test_the_api_sends_updates(env, monkeypatch):
    import api

    need("api", "temporal_client", 10)
    SupportWorkflow = need("workflows", "SupportWorkflow", 8)

    async def test_server():
        return env.client

    monkeypatch.setattr(api, "temporal_client", test_server)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test")
    seen, calls = [], []
    async with Worker(env.client, task_queue=config.TASK_QUEUE, workflows=[SupportWorkflow],
                      activities=stand_ins(seen, calls)):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://support") as http:
            for text in ("where is my order?", "thanks!"):
                response = await http.post("/chats/order-step10/messages", json={"text": text})
                assert response.status_code == 200, response.text
        assert calls, "POST /chats/.../messages should send an update to the workflow, not use SupportChat"
        description = await env.client.get_workflow_handle("support-order-step10").describe()
        assert description.status.name == "RUNNING", "use support-{order_id} as the workflow ID"
        assert seen == [1, 3, 5, 7], "both messages should land on the same conversation: USE_EXISTING"
