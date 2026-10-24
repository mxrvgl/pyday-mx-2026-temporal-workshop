"""The checkout, as most of us would write it first: one step after the other.

It works, as long as nothing goes wrong. Try `make payments-down`, `make inventory-refuse`
or `make kill-checkout` while an order is on its way, and look at what's left behind.
"""

from steps import Order, charge_card, reserve_stock, save_order, ship_order


async def place_order(order: Order) -> str:
    await save_order(order)
    await reserve_stock(order)
    order.charge_id = await charge_card(order)
    await ship_order(order)

    order.status = "COMPLETED"
    await save_order(order)
    return order.status
