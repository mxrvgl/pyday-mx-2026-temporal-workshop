"""The support API: POST /chats/{order_id}/messages to talk to the agent about an order."""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

import config
from workflows import SupportWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
app = FastAPI(title="Support")
_temporal: Client | None = None


async def temporal_client() -> Client:
    global _temporal
    if _temporal is None:
        _temporal = await Client.connect(config.TEMPORAL_ADDRESS)
    return _temporal


class Message(BaseModel):
    text: str


@app.post("/chats/{order_id}/messages")
async def send_message(order_id: str, message: Message) -> list[dict]:
    """One customer message in; the agent's replies, and the tools it used, out."""
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set: put it in .env, then make up")
    client = await temporal_client()
    handle = await client.start_workflow(
        SupportWorkflow.run,
        order_id,
        id=f"support-{order_id}",
        task_queue=config.TASK_QUEUE,
        # Already chatting about this order? Then this message joins that conversation.
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    try:
        return await handle.execute_update(SupportWorkflow.send_message, message.text)
    except Exception as err:
        raise HTTPException(503, f"support is unavailable: {type(err).__name__}: {err}") from None


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
