"""The support worker: runs SupportWorkflow, the call to Claude, and the tools.

It asks for work on the "support" task queue. If it dies, the conversation waits
on the Temporal server until a worker is back.
"""

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
