"""The support API: POST /chats/{order_id}/messages to talk to the agent about an order."""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
from chat import SupportChat

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
app = FastAPI(title="Support")
chats: dict[str, SupportChat] = {}


class Message(BaseModel):
    text: str


@app.post("/chats/{order_id}/messages")
async def send_message(order_id: str, message: Message) -> list[dict]:
    """One customer message in; the agent's replies, and the tools it used, out."""
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set: put it in .env, then make up")
    chat = chats.setdefault(order_id, SupportChat(order_id))
    try:
        return await chat.send(message.text)
    except Exception as err:
        raise HTTPException(503, f"support is unavailable: {type(err).__name__}: {err}") from None


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
