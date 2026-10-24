"""Claude: the system prompt, the tools it may ask for, and one call to the API."""

import anthropic

import config

SYSTEM_PROMPT = """\
You are the support assistant of Durable Threads, an online clothing store. The customer \
opened this chat about one of their orders. Help with questions about it and handle refunds.
- Look the order up before answering. Never invent details.
- Before refunding, call check_refund_policy. Call issue_refund only if it says "approve"; \
otherwise explain the reason kindly.
- One to three short sentences, plain text. Reply in the customer's language."""

TOOLS = [
    {"name": "lookup_order", "description": "The order: item, amount, date, whether it was charged or refunded."},
    {"name": "get_purchase_history", "description": "The customer's purchases and refunds, newest first."},
    {"name": "check_refund_policy", "description": "Whether this order can be refunded, and why."},
    {"name": "issue_refund", "description": "Refund this order in full. Only after check_refund_policy."},
]
for tool in TOOLS:
    tool["input_schema"] = {"type": "object", "properties": {}}  # no arguments: the order comes from the chat

# How many times the Anthropic SDK retries a failed call on its own.
SDK_RETRIES = 2


async def call_claude(messages: list[dict]) -> list[dict]:
    """Send the conversation so far; get Claude's next turn back as plain dicts."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=SDK_RETRIES)
    response = await claude.messages.create(
        model=config.AGENT_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
        output_config={"effort": "low"},
    )
    if response.stop_reason == "refusal":
        return [{"type": "text", "text": "Sorry, I can't help with that one."}]
    return [as_dict(block) for block in response.content]


def as_dict(block) -> dict:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return block.model_dump(exclude_none=True)  # thinking blocks go back unchanged
