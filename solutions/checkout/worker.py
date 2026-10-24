"""The worker: the process that runs your workflows and activities.

It connects to Temporal and asks for work on the "checkout" task queue. If it dies,
the work waits on the Temporal server until a worker is back.
"""

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
