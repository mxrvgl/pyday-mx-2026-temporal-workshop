# Durable Threads: a Temporal workshop

A small shop sells T-shirts. When someone buys one, the **checkout** service reserves it in the warehouse, charges the customer's card, and ships it. Every step is a call to another service, and any of them can fail.

In 30 minutes you'll move that checkout onto [Temporal](https://temporal.io), and watch how it behaves when those services fail.

## Before the session

You should already have Docker, Docker Compose and `make` installed from [SETUP.md](SETUP.md). From this repository, check that everything is in place:

```sh
make preflight   # checks Docker, Compose, memory and ports, and says what to fix
make setup       # downloads and builds everything: a few minutes the first time
make up && make buy && make down   # a quick smoke test
```

You're ready when:
- **`make preflight`** ends with `Ready. Next: make setup`, with no `✗` above it;
- **`make setup`** ends with `Ready. Start with: make up`;
- **`make buy`** prints `COMPLETED`.

### A virtualenv for your editor

The code runs in containers, but your editor needs the libraries locally to autocomplete `temporalio` and to stop underlining its imports. With Python 3.11 or later (3.13 matches the containers):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r checkout/requirements.txt
python -c "import temporalio; print(temporalio.__version__)"   # prints 1.34.0
```

Then point your editor at `.venv/bin/python` (in VS Code: *Python: Select Interpreter*).

---

## The shop

```
                        reserve, ship, release
 make buy ─────► ┌──────────────┐ ───────────────────────► ┌─────────────┐
 (POST /orders)  │   checkout   │                          │  inventory  │
                 │ ← you change │      charge, refund      │ (warehouse) │
                 │   this one   │ ───────────┐             └──────┬──────┘
                 └──────┬───────┘            ▼                    │
                        │            ┌──────────────────┐         │
                 orders │            │ payment provider │         │ stock
                        │            │    ("Stripe")    │         │
                        ▼            └──────────────────┘         ▼
                 ┌──────────────────────────────────────────────────────┐
                 │                       postgres                       │
                 └──────────────────────────────────────────────────────┘
```

- **`checkout/`** is the only code you change:
  - `steps.py`: one function per call to the outside world. `save_order`, `reserve_stock`, `charge_card` and `ship_order`, plus the two that undo a step, `release_stock` and `refund_charge`.
  - `checkout.py`: `place_order()`, the steps one after the other.
  - `api.py`: `POST /orders` calls `place_order()`.
- **`inventory/`** and **`payment_provider/`** are services that other people run, a warehouse and a Stripe-like provider. You don't change them, but you can make them misbehave.
- **`solutions/checkout/`** is the checkout as it looks at the end. **`scripts/`** holds the tooling behind the `make` commands.

## Step 0: run it, then break it (5 min)

Start the shop, and leave the board running in a second terminal pane:

```sh
make up        # the shop: checkout, inventory, payment provider, database
make board     # (second pane) each service's health, and every order as each service sees it
make buy       # alice buys a T-shirt
make status    # what checkout, inventory and the provider each know about that order
```

Now read `checkout/checkout.py`. It's what most of us would write:

```python
async def place_order(order: Order) -> str:
    await save_order(order)
    await reserve_stock(order)
    order.charge_id = await charge_card(order)
    await ship_order(order)

    order.status = "COMPLETED"
    await save_order(order)
    return order.status
```

### Fault switches

The warehouse and the payment provider have switches that make them misbehave. Each one takes effect straight away and behaves the same every time. You'll use them throughout the workshop:

| | |
| --- | --- |
| `make payments-down` / `make payments-up` | The payment provider answers 503 to everything |
| `make payments-flaky` | The next 3 payment calls fail |
| `make payments-slow` | Payments take 5s, longer than checkout waits |
| `make inventory-down` / `make inventory-up` | The same, for the warehouse |
| `make inventory-refuse` | The warehouse refuses to ship |
| `make faults-off` | Everything back to normal |
| `make buy CUSTOMER=bob` | Bob's card is always declined |
| `make kill-checkout`, `make kill-worker` | Kill a process outright (SIGKILL); `make start-…` brings it back |

Try each of these, and run `make status` after each one. For every order, ask: where did the T-shirt end up, and where did the money go?

```sh
make payments-down && make buy && make payments-up
make buy CUSTOMER=bob
make inventory-refuse && make buy && make faults-off
make buy        # and while it's waiting, from another pane: make kill-checkout, then make start-checkout
```

**What just happened**
- **Payments down:** the order failed, and its T-shirt is still reserved. Nothing will ever release it.
- **Bob:** the card was declined, and the T-shirt is stuck again.
- **Warehouse refused:** the card was charged, and nothing shipped.
- **Checkout killed mid-order:** the card was charged, and the order says PENDING forever. Nobody remembers to finish it.

You could fix each one by hand: retry loops, a table of where each order got to, a cron job for stuck orders, undo code. Instead, you'll hand the checkout to Temporal.

## Three words before you start

- **Activity**: a function that talks to the outside world, like each function in `steps.py`.
- **Workflow**: the function that calls the activities in order, like `place_order`.
- **Worker**: your process. It runs workflows and activities when the Temporal server hands it work.

**Falling behind?** `make solution` shows how your `checkout/` differs from the finished one (`FILE=workflows.py` for one file). `make up-temporal SOLUTION=1` runs the finished version, and `make up-temporal SOLUTION=0` (or `make down`) goes back to yours.

---

## Step 1: the steps become activities (2 min)

In `checkout/steps.py`, import `activity` and put `@activity.defn` on each of the six step functions:

```python
from temporalio import activity

@activity.defn
async def save_order(order: Order) -> None:
    ...
```

```sh
make buy
make check STEP=1
```

**What just happened**
- `make buy` behaves exactly as before. The decorator doesn't change what a function does when you call it; it marks the function as something Temporal is allowed to run, and retry.

## Step 2: the checkout becomes a workflow (5 min)

Create `checkout/workflows.py`. Put it next to `place_order()` as you read:

```python
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from steps import Order, charge_card, reserve_stock, save_order, ship_order

TIMEOUT = timedelta(seconds=10)  # how long one attempt of a step may take


@workflow.defn
class CheckoutWorkflow:
    @workflow.run
    async def run(self, order: Order) -> str:
        await workflow.execute_activity(save_order, order, start_to_close_timeout=TIMEOUT)
        await workflow.execute_activity(reserve_stock, order, start_to_close_timeout=TIMEOUT)
        order.charge_id = await workflow.execute_activity(charge_card, order, start_to_close_timeout=TIMEOUT)
        await workflow.execute_activity(ship_order, order, start_to_close_timeout=TIMEOUT)

        order.status = "COMPLETED"
        await workflow.execute_activity(save_order, order, start_to_close_timeout=TIMEOUT)
        return order.status
```

```sh
make check STEP=2
```

**What just happened**
- It's the same lines as `place_order()`, with each call wrapped in `workflow.execute_activity`. That wrapper doesn't call the function. It asks Temporal to run it, on whichever worker is free, and waits for the result.
- `start_to_close_timeout` limits how long one attempt may take before Temporal gives up on it and tries again.
- **Workflow code must be deterministic.** After a crash, Temporal rebuilds a workflow by running its code again and handing it the results it recorded. So workflows make no network calls and don't use `datetime.now()` or `random()`; that belongs in activities. `imports_passed_through` tells Temporal's safety sandbox that these imports are fine.
- Nothing runs this workflow yet: that takes a worker, the next step.

## Step 3: a worker to run it (3 min)

Create `checkout/worker.py`:

```python
import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

import config
from steps import charge_card, refund_charge, release_stock, reserve_stock, save_order, ship_order
from workflows import CheckoutWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    client = await Client.connect(config.TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=config.TASK_QUEUE,
        workflows=[CheckoutWorkflow],
        activities=[save_order, reserve_stock, charge_card, ship_order, release_stock, refund_charge],
    )
    logging.info("Worker started on task queue %r", config.TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

Then start Temporal and your worker, run a workflow straight from Temporal's CLI, and find it in the UI:

```sh
make up-temporal    # adds the Temporal server and your worker
make try-workflow   # runs one CheckoutWorkflow and prints its result
make ui             # http://localhost:8233: click the workflow, then look at its history
make check STEP=3
```

**What just happened**
- Your worker connected to Temporal and asked for work on the `checkout` task queue. `try-workflow` put a workflow on that queue, and your worker ran it, without the API being involved.
- The **event history** in the UI has a group of events for each step, in order. That history is what Temporal replays after a crash. It's also the answer to "what happened to this order?".

## Step 4: the API runs the workflow (3 min)

In `checkout/api.py`, add a Temporal client:

```python
from temporalio.client import Client, WorkflowFailureError

from workflows import CheckoutWorkflow

_temporal: Client | None = None


async def temporal_client() -> Client:
    global _temporal
    if _temporal is None:
        _temporal = await Client.connect(config.TEMPORAL_ADDRESS)
    return _temporal
```

In `create_order`, replace

```python
    try:
        status = await place_order(order)
    except Exception as err:
        order.status = "FAILED"
        await save_order(order)
        status = f"FAILED: {type(err).__name__}: {err}"
```

with

```python
    client = await temporal_client()
    try:
        status = await client.execute_workflow(
            CheckoutWorkflow.run, order, id=order.order_id, task_queue=config.TASK_QUEUE
        )
    except WorkflowFailureError as err:
        status = f"FAILED: {err.cause.cause or err.cause}"  # the activity's own error, if there is one
```

Delete `checkout.py`, and the `place_order` and `save_order` imports. The API reloads by itself when you save. Only the worker needs `make restart-worker`.

```sh
make buy            # then find the order in the UI
make check STEP=4
```

**What just happened**
- Each order is now a workflow, and its **workflow ID is the order ID**. Temporal never runs two workflows with the same ID at once, so a double-clicked "Buy" button can't start two checkouts.

## Replay the breaks (4 min)

The same failures as in step 0. Before each one, guess what will happen. Keep the UI and `make board` open.

```sh
make payments-down && make buy   # then, from another pane, after a few seconds: make payments-up

make buy                         # while it's waiting, from another pane:
make kill-worker                 # then: make start-worker
make status

make buy                         # while it's waiting, from another pane:
make kill-checkout               # then: make start-checkout
make status

make buy CUSTOMER=bob            # watch the UI for a while. Then Ctrl-C, and Terminate the workflow in the UI
```

**What just happened**
- **Payments down:** the order waited instead of failing. In the UI, `charge_card` shows attempt 2, 3, 4… and once payments was back, the next attempt went through and the order finished on its own. You wrote no retry loop.
- **Worker killed:** the order carried on from the step it was on, not from the start. Stock was reserved once, and there's one charge: retried calls reuse the same idempotency key. (It can take up to 10s to resume: that's the `start_to_close_timeout` running out on the attempt that died with the worker.)
- **Checkout killed:** the HTTP request was lost, but the order wasn't. It lives in Temporal, not in the API process, so `make status` shows it completed.
- **Bob:** Temporal retries a declined card again and again, because by default it retries everything. The next step fixes that.

## Step 5: retry only what's worth retrying (3 min)

In `workflows.py`:

```python
from temporalio.common import RetryPolicy

# Down, slow or 5xx: retry with backoff (1s, 2s, 4s, then every 5s), for as long as it takes.
# A business "no" won't change by asking again. These are exception names from steps.py.
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=5),
    non_retryable_error_types=["CardDeclined", "OutOfStock", "ShippingRefused"],
)
```

Add `retry_policy=RETRY` to every `execute_activity` call, then `make restart-worker` (the worker doesn't reload on its own).

```sh
make buy CUSTOMER=bob
make status
make check STEP=5
```

**What just happened**
- Bob's order failed after a single attempt. When an activity raises, the workflow sees an error whose **type** is the exception's class name, so the plain exceptions in `steps.py` decide what's worth retrying. Outages are still retried.
- But `make status` shows the T-shirt still reserved: failing cleanly isn't the same as cleaning up. That's the last step.

## Step 6: undo what was done (5 min)

When a step says no for good, undo the ones that succeeded. Replace `run` in `workflows.py` with the version below, and import `refund_charge`, `release_stock`, `ActivityError` and `ApplicationError`:

```python
from temporalio.exceptions import ActivityError, ApplicationError

    @workflow.run
    async def run(self, order: Order) -> str:
        await workflow.execute_activity(save_order, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
        try:
            await workflow.execute_activity(reserve_stock, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
            order.charge_id = await workflow.execute_activity(charge_card, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
            await workflow.execute_activity(ship_order, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
            order.status = "COMPLETED"

        except ActivityError as err:
            # A step said no for good: undo what was done, newest first.
            if order.charge_id:
                await workflow.execute_activity(refund_charge, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
            await workflow.execute_activity(release_stock, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
            declined = isinstance(err.cause, ApplicationError) and err.cause.type == "CardDeclined"
            order.status = "DECLINED" if declined else "CANCELLED"

        await workflow.execute_activity(save_order, order, start_to_close_timeout=TIMEOUT, retry_policy=RETRY)
        return order.status
```

`make restart-worker`, then try both of the cases that left a mess in step 0, with `make status` after each:

```sh
make buy CUSTOMER=bob
make inventory-refuse && make buy && make faults-off

make inventory-refuse && make buy   # as soon as the shipment is refused, from another pane: make payments-down
make payments-up && make faults-off # after watching the UI for a bit

make check STEP=6
```

**What just happened**
- **Bob:** DECLINED, and the T-shirt was released. No money moved, so there was nothing to refund.
- **Warehouse refused:** CANCELLED. The card was charged, then refunded, and the T-shirt was released, newest step first.
- **Payments down during the refund:** the refund waited and went through once payments was back. Undo steps are activities too, so they get the same retries. This pattern is a **saga**: a `try/except` that undoes what was done, and it's as durable as the rest of the workflow.

### What you didn't write

| By hand | With Temporal |
| --- | --- |
| Retry loops around every call | A `RetryPolicy` |
| A table of where each order got to, and code to resume from it | The workflow history: a new worker resumes at the exact step |
| A cron job that finds stuck orders | Nothing. Orders don't get stuck, they wait |
| Undo logic spread across services | A `try/except` in one function |

---

## If you have time left

- **Ask the workflow how it's doing.** Add a `@workflow.query` that returns the order's status, and make `GET /orders/{id}` query the workflow (`client.get_workflow_handle(order_id).query(...)`) instead of reading the database.
- **Next: [Part 2, the support agent](#part-2-the-support-agent).** A refund agent built on Claude gets the same treatment.

---

# Part 2: the support agent

Customers can chat with a support agent built on Claude about an order, and ask for a refund. It's the checkout's story again, with an AI agent: a loop in one process's memory, calling services that fail now and then. About 30 minutes. You don't need to have finished Part 1.

## Before you start

You need your own Anthropic API key (from [console.anthropic.com](https://console.anthropic.com)). The agent uses Claude Haiku, so the whole of Part 2 costs a few cents. Put the key in `.env`, then check it works:

```sh
cp -n .env.example .env       # if you don't have a .env yet
# edit .env: ANTHROPIC_API_KEY=sk-ant-...
make check-key                # prints "✓ Claude answered"
make up                       # restarts support with your key
```

Without a key, the chat answers with an error that says so.

## The support service

```
 make chat ──────► ┌──────────────┐      the conversation      ┌──────────┐
 (POST /chats/     │   support    │ ─────────────────────────► │  Claude  │
  {order}/messages)│ ← you change │                            └──────────┘
                   │   this one   │      ledger, refund        ┌──────────────────┐
                   │              │ ─────────────────────────► │ payment provider │
                   └──────┬───────┘                            └──────────────────┘
                          │ reads checkout's orders, writes its own refunds
                          ▼
                   ┌──────────────┐
                   │   postgres   │
                   └──────────────┘
```

- **`support/`** is the only code you change in Part 2:
  - `tools.py`: the four tools the agent can use: `lookup_order`, `get_purchase_history`, `check_refund_policy` and `issue_refund`. They get the order from the chat, never from the model, so the agent can only see and refund the order the chat is about.
  - `tools.py` also holds the **refund policy**, as code: refunds within 30 days only, none for accounts with 3 or more refunds in the 90 days before today, and anything over MXN 1,000 goes to a person. `issue_refund` checks it again itself, so no conversation can talk the agent past it.
  - `agent.py`: `call_claude()`, one call to Claude with the system prompt and the tools.
  - `chat.py`: `SupportChat.send()`, the agent loop.
  - `api.py`: `POST /chats/{order_id}/messages`. It keeps one `SupportChat` per order, in a dict.
- **`solutions/support/`** is the service as it looks at the end.

## Step 0: chat, then break it (5 min)

```sh
make buy && make chat                     # alice: "the t-shirt is too small, can I get a refund?"
make status                               # the provider recorded the refund
make buy CUSTOMER=mallory && make chat    # mallory asks for a refund too
make chat ID=old-alice-1                  # alice's order from 45 days ago
```

The tools the agent uses show up in the chat as `⚙ lookup_order`. Now read `support/chat.py`:

```python
    async def send(self, text: str) -> list[dict]:
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
        ...
```

Call Claude, run the tools it asks for, and repeat until it answers in plain text. Now break it:

```sh
make buy && make chat     # ask for a refund. While the agent works, from another pane:
make kill-support         # then: make start-support, and in the chat ask "did you refund it?"
make status               # did the refund happen?

make buy && make payments-down && make chat   # ask for a refund
make payments-up
```

**What just happened**
- **Mallory and the old order:** refused, by code. The policy said no, and the agent explained why.
- **Support killed mid-answer:** the conversation is gone. Ask "did you refund it?" and the agent has forgotten you asked for a refund: the customer has to start over. If the kill came late, `make status` shows the refund went through, and the customer was never told.
- **Payments down:** the refund failed, and the customer saw an error instead of an answer. Asking again is up to them.

## Step 7: Claude and the tools become activities (3 min)

In `support/tools.py`, put `@activity.defn` on the four tools, as in step 1: `lookup_order`, `get_purchase_history`, `check_refund_policy` and `issue_refund`. In `support/agent.py`, put it on `call_claude`:

```python
from temporalio import activity


@activity.defn
async def call_claude(messages: list[dict]) -> list[dict]:
```

In `agent.py`, also set `SDK_RETRIES = 0`.

```sh
make check STEP=7
```

**What just happened**
- The call to Claude is an activity like any other: it talks to the outside world, it can fail, and it can be retried.
- The Anthropic SDK retries a failed call twice on its own, silently. With `SDK_RETRIES = 0`, Temporal does the retrying instead, so every attempt shows in the UI.

## Step 8: the conversation becomes a workflow (7 min)

Create `support/workflows.py`. It's the loop from `SupportChat.send`, almost line for line:

```python
import asyncio
import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agent import call_claude
    from tools import TOOLS_BY_NAME

RETRY = RetryPolicy(maximum_interval=timedelta(seconds=5))
IDLE = timedelta(minutes=30)


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
```

```sh
make check STEP=8
```

**What just happened**
- The conversation, `self.messages`, is now **workflow state**. Temporal records every activity's result, so after a crash it rebuilds the conversation exactly as it was.
- Each customer message is an **Update**: a request that runs inside the workflow and returns a result to whoever sent it, here the agent's replies. While it runs, it calls activities, just like `run` does. The lock keeps two quick messages from being answered at the same time.
- `run` keeps the conversation open. `wait_condition(..., timeout=IDLE)` is a **durable timer**: the conversation closes after 30 minutes without a message, even if every worker was down in between.
- `RETRY` has no non-retryable errors: a rate limit, an overloaded model or a provider outage is worth waiting out.

## Step 9: a worker for support (3 min)

Create `support/worker.py`. It's checkout's worker, with support's workflow and activities:

```python
import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

import config
from agent import call_claude
from tools import TOOLS_BY_NAME
from workflows import SupportWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    client = await Client.connect(config.TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=config.TASK_QUEUE,
        workflows=[SupportWorkflow],
        activities=[call_claude, *TOOLS_BY_NAME.values()],
    )
    logging.info("Worker started on task queue %r", config.TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

```sh
make up-temporal    # starts Temporal, and every worker that has a worker.py
make logs-worker    # "Worker started on task queue 'support'". Ctrl-C to stop following
make check STEP=9
```

**What just happened**
- The support worker listens on its own task queue, `support`. If you did Part 1, the checkout worker runs next to it, on `checkout`.

## Step 10: the API sends Updates (4 min)

In `support/api.py`, add a Temporal client, as in step 4:

```python
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from workflows import SupportWorkflow

_temporal: Client | None = None


async def temporal_client() -> Client:
    global _temporal
    if _temporal is None:
        _temporal = await Client.connect(config.TEMPORAL_ADDRESS)
    return _temporal
```

In `send_message`, replace

```python
    chat = chats.setdefault(order_id, SupportChat(order_id))
    try:
        return await chat.send(message.text)
```

with

```python
    client = await temporal_client()
    handle = await client.start_workflow(
        SupportWorkflow.run,
        order_id,
        id=f"support-{order_id}",
        task_queue=config.TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    try:
        return await handle.execute_update(SupportWorkflow.send_message, message.text)
```

Delete the `chats` dict, the `SupportChat` import, and `chat.py`.

```sh
make buy && make chat      # then open support-order-… in the UI
make check STEP=10
```

**What just happened**
- One workflow per conversation, with the ID `support-{order_id}`. `USE_EXISTING` means a message about an order that already has a conversation joins it, instead of starting a new one.
- In the UI, the history of `support-order-…` has every call to Claude and every tool call, with its inputs and outputs. When someone asks why the agent refunded an order, that's the record.

## Replay the breaks (5 min)

Before each one, guess what will happen.

```sh
make buy && make chat      # ask for a refund. While the agent works, from another pane:
make kill-support          # then make start-support, and in the chat ask "did you refund it?"

make buy && make chat      # ask for a refund again. As soon as you've sent it, from another pane:
make kill-support-worker   # wait a few seconds, then: make start-support-worker

make buy && make payments-down && make chat   # ask for a refund. From another pane, after a few seconds: make payments-up
```

Then break the key: put a wrong `ANTHROPIC_API_KEY` in `.env`, run `make up-temporal`, and send a message. Watch `make logs-worker`. Then put the right key back and run `make restart-worker`, leaving the chat waiting.

**What just happened**
- **Support killed:** that message got an error, but the conversation lives in Temporal, not in the API process. The agent finished the refund, and when you asked, it remembered.
- **Worker killed:** the chat waited. The new worker picked up the conversation where the old one stopped. (It can take up to 30s, or 60s during a call to Claude: that's the `start_to_close_timeout` running out on the attempt that died with the worker.)
- **Payments down:** `issue_refund` was retried until the provider was back, and then the answer arrived. In the UI, it shows attempt 2, 3, 4…
- **Wrong key:** `call_claude` failed and was retried. Once the key was fixed, the same message got its answer.
- Throughout, `make status` shows one refund per order: the agent refunds with the same idempotency key that checkout uses.

### What you didn't write

| By hand | With Temporal |
| --- | --- |
| A database of conversations, and code to save and reload them | The conversation is workflow state |
| Retries around Claude and every tool | A `RetryPolicy` |
| A log of what the agent did and why | The workflow history |
| A job that closes idle conversations | A durable timer |

## Go further: a person approves large refunds

The policy sends refunds over MXN 1,000 to a person, but right now the agent can only say "we'll email you". With Temporal, the workflow can wait for that person's decision. Sketch:

- When the policy says `review`, record in `SupportWorkflow` that a review is pending.
- Add a **signal**, `review_refund(approved: bool)`: a one-way message to the workflow. A small admin endpoint in `api.py` sends it with `handle.signal(SupportWorkflow.review_refund, True)`.
- In `run`, wait for it with `workflow.wait_condition(lambda: self.decision is not None, timeout=timedelta(days=2))`. On a yes, run a refund activity that skips the amount check. On a timeout, reject the request.
- Add the outcome to the conversation.

Try it with a hoodie: `make buy SKU=HOODIE-L && make chat`. Nothing runs while the workflow waits: no process holds a timer in memory, and no cron job checks for expired reviews.

---

## Reference

| | |
| --- | --- |
| `make preflight`, `make setup` | Check the machine; download and build everything |
| `make up`, `make up-temporal`, `make down`, `make reset` | Start; start with Temporal; stop and wipe; start fresh |
| `make buy` (`CUSTOMER=bob`), `make status`, `make board` | Buy; one order across all services; the live view |
| `make stock`, `make ledger`, `make psql` | Inventory, the provider's records, the database |
| `make restart-worker`, `make logs-worker`, `make ui` | After a code change; every attempt and retry; Temporal UI |
| `make check STEP=n`, `make solution`, `make test` | Check your steps; diff with the solution; check the solution |
| `make chat` (`ID=old-alice-1`), `make check-key` | Part 2: chat with support about the last order; check your API key |
| `make kill-support`, `make kill-support-worker` | Part 2: kill the support API or its worker; `make start-…` brings it back |
| `make solution SERVICE=support` | Part 2: diff your `support/` with the solution |

The fault switches are listed in [Step 0](#fault-switches).

Ports: checkout 8000, inventory 8001, payment provider 8002, support 8003 (each has API docs at `/docs`), Postgres 5432, Temporal 7233, UI 8233.

### Troubleshooting

- **`make buy` hangs after step 4.** Is your worker running? Check `make logs-worker`. After changing workflow or step code, run `make restart-worker`.
- **`make buy CUSTOMER=bob` never finishes (before step 5).** Expected: a declined card is retried forever by default. Press Ctrl-C, and terminate the workflow in the UI.
- **"Nondeterminism" errors after you change the workflow.** Workflows started on the old code are replayed against the new code. Terminate them in the UI, or `make reset`.
- **`make chat` says `ANTHROPIC_API_KEY is not set`.** Put your key in `.env`, then `make up` (or `make up-temporal` from step 9 on), so the containers pick it up.
- **The chat waits forever after step 10.** Is the support worker running? Check `make logs-worker`. After changing `workflows.py`, `tools.py` or `agent.py`, run `make restart-worker`.
- **A port is taken.** `make preflight` says which one. Stop whatever is using it (another demo, a local Temporal or Postgres), then `make up`.
- **`docker compose` says `!override` is unknown, or `--wait` isn't a flag.** Your Compose is older than 2.24.4: update Docker Desktop, or the `docker-compose-plugin` package.
