"""SupportWorkflow: the conversation from SupportChat, run by Temporal.

The loop is the same. What changes: the conversation is workflow state, Claude
and the tools are activities, and each customer message is an Update, a request
that changes the workflow and returns a result (the agent's replies).
"""

import asyncio
import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agent import call_claude
    from tools import TOOLS_BY_NAME

# Rate limits, an overloaded model, the payment provider down: keep trying, the customer can wait.
RETRY = RetryPolicy(maximum_interval=timedelta(seconds=5))
IDLE = timedelta(minutes=30)  # the conversation closes after this long without a message


@workflow.defn
class SupportWorkflow:
    @workflow.init
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        self.messages: list[dict] = []
        self.turns = 0
        self.one_at_a_time = asyncio.Lock()

    @workflow.run
    async def run(self, order_id: str) -> None:
        # Stay open while the customer keeps talking: a durable timer, reset by every message.
        while True:
            turns = self.turns
            try:
                await workflow.wait_condition(lambda: self.turns != turns, timeout=IDLE)
            except asyncio.TimeoutError:
                break
        await workflow.wait_condition(workflow.all_handlers_finished)

    @workflow.update
    async def send_message(self, text: str) -> list[dict]:
        async with self.one_at_a_time:
            self.turns += 1
            replies = []
            self.messages.append({"role": "user", "content": text})
            while True:
                content = await workflow.execute_activity(
                    call_claude, self.messages, start_to_close_timeout=timedelta(seconds=60), retry_policy=RETRY
                )
                self.messages.append({"role": "assistant", "content": content})
                replies += [{"agent": b["text"]} for b in content if b["type"] == "text"]

                tool_calls = [b for b in content if b["type"] == "tool_use"]
                if not tool_calls:
                    return replies
                results = []
                for call in tool_calls:
                    replies.append({"tool": call["name"]})
                    result = await workflow.execute_activity(
                        TOOLS_BY_NAME[call["name"]], self.order_id, start_to_close_timeout=timedelta(seconds=30), retry_policy=RETRY
                    )
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result)})
                self.messages.append({"role": "user", "content": results})
