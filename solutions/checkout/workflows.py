"""CheckoutWorkflow: place_order() from checkout.py, run by Temporal.

Workflow code must be deterministic: after a crash, Temporal runs it again and hands
it the results it recorded, to rebuild where it was. So no network calls, no
datetime.now(), no random() in here. Those belong in activities (steps.py).
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from steps import Order, charge_card, refund_charge, release_stock, reserve_stock, save_order, ship_order

TIMEOUT = timedelta(seconds=10)  # how long one attempt of a step may take

# Down, slow or 5xx: retry with backoff (1s, 2s, 4s, then every 5s), for as long as it takes.
# A business "no" won't change by asking again. These are exception names from steps.py.
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=5),
    non_retryable_error_types=["CardDeclined", "OutOfStock", "ShippingRefused"],
)


@workflow.defn
class CheckoutWorkflow:
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
