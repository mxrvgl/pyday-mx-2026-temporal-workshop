"""SupportChat: one conversation about one order, kept in this process's memory."""

import json

from agent import call_claude
from tools import TOOLS_BY_NAME


class SupportChat:
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        self.messages: list[dict] = []

    async def send(self, text: str) -> list[dict]:
        """The agent loop: call Claude, run the tools it asks for, repeat until it just talks."""
        replies, start = [], len(self.messages)
        self.messages.append({"role": "user", "content": text})
        try:
            while True:
                content = await call_claude(self.messages)
                self.messages.append({"role": "assistant", "content": content})
                replies += [{"agent": b["text"]} for b in content if b["type"] == "text"]

                tool_calls = [b for b in content if b["type"] == "tool_use"]
                if not tool_calls:
                    return replies
                results = []
                for call in tool_calls:
                    replies.append({"tool": call["name"]})
                    result = await TOOLS_BY_NAME[call["name"]](self.order_id)
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result)})
                self.messages.append({"role": "user", "content": results})
        except Exception:
            del self.messages[start:]  # forget the half-finished turn, so the next message starts clean
            raise
