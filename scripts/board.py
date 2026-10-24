"""`make board`: the services, their faults, and what each one knows about your orders."""

import os
import time
from pathlib import Path

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

TEMPORAL_UI = os.getenv("TEMPORAL_UI_URL", "http://temporal:8233")
LAST_ORDER = Path("/workspace/.last-order")
STATUS_STYLE = {"COMPLETED": "green", "PENDING": "cyan", "DECLINED": "yellow", "CANCELLED": "yellow", "FAILED": "red"}
STOCK_STYLE = {"SHIPPED": "green", "RESERVED": "yellow", "RELEASED": "dim"}


def get(url: str):
    """The JSON at url, or None if the service is down or failing."""
    try:
        response = httpx.get(url, timeout=1)
        return response.json() if response.status_code < 500 else None
    except httpx.HTTPError:
        return None


def up(url: str) -> bool:
    try:
        httpx.get(url, timeout=1)
        return True
    except httpx.HTTPError:
        return False


class Board:
    def __init__(self, checkout: str, inventory: str, provider: str, support: str) -> None:
        self.checkout, self.inventory, self.provider, self.support = checkout, inventory, provider, support
        # The last answer from each service, so the board stays useful while one is down.
        self.orders: list[dict] = []
        self.reservations: dict[str, dict] = {}
        self.ledger: dict[str, list[dict]] = {}

    def render(self) -> Group:
        return Group(self.services(), self.order_table())

    def services(self) -> Panel:
        grid = Table.grid(padding=(0, 2))
        for name, url in [("checkout", f"{self.checkout}/docs"), ("inventory", f"{self.inventory}/faults"),
                          ("payment provider", f"{self.provider}/faults"), ("support", f"{self.support}/health"),
                          ("temporal", TEMPORAL_UI)]:
            is_up = up(url)
            answer = get(url) if is_up and url.endswith("/faults") else None
            faults = [k for k, v in answer.items() if v] if answer else []
            state = "[green]● UP[/]" if is_up else "[dim]○ off[/]" if name == "temporal" else "[bold red]● DOWN[/]"
            grid.add_row(state, name, f"[bold red]{', '.join(faults)}[/]" if faults else "")
        return Panel(grid, title="Services", title_align="left")

    def order_table(self) -> Panel:
        stale = []
        if (orders := get(f"{self.checkout}/orders")) is not None:
            self.orders = [o for o in orders if not o["order_id"].startswith("old-")]  # seeded history for Part 2
        else:
            stale.append("checkout")
        if (reservations := get(f"{self.inventory}/reservations")) is not None:
            self.reservations = {r["order_id"]: r for r in reservations}
        else:
            stale.append("inventory")
        if (ledger := get(f"{self.provider}/v1/ledger")) is not None:
            self.ledger = {}
            for entry in ledger:
                self.ledger.setdefault(entry["order_id"], []).append(entry)
        else:
            stale.append("payments")

        last = LAST_ORDER.read_text().strip() if LAST_ORDER.exists() else ""
        table = Table(box=None, padding=(0, 2), header_style="bold")
        for column in ("", "order", "customer", "checkout says", "stock", "money"):
            table.add_column(column, no_wrap=True)
        for order in self.orders:
            order_id = order["order_id"]
            status = order["status"]
            stock = self.reservations.get(order_id, {}).get("status", "—")
            table.add_row(
                "[bold]▶[/]" if order_id == last else "",
                f"[bold]{order_id}[/]" if order_id == last else order_id,
                order["customer_id"],
                f"[{STATUS_STYLE.get(status, 'white')}]{status}[/]",
                f"[{STOCK_STYLE.get(stock, 'white')}]{stock}[/]",
                money(self.ledger.get(order_id, [])),
            )
        title = "Orders, newest first" + (f" [red](last known: {', '.join(stale)} unreachable)[/]" if stale else "")
        body = table if self.orders else "[dim]No orders yet: make buy[/]"
        return Panel(body, title=title, title_align="left")


def money(entries: list[dict]) -> str:
    """What the provider did for one order, e.g. 'charged 499.00, refunded'."""
    if not entries:
        return "[dim]none moved[/]"
    parts = []
    for e in entries:
        verb = {"charge": "charged", "refund": "refunded"}[e["operation"]]
        if e["result"] != "OK":
            parts.append(f"[yellow]{e['operation']} {e['result'].lower()}[/]")
        else:
            parts.append(f"{verb} {e['amount'] / 100:,.2f}")
        if e["replays"]:
            parts.append(f"[yellow]+{e['replays']} duplicate absorbed[/]")
    return ", ".join(parts)


def run(console: Console, checkout: str, inventory: str, provider: str, support: str) -> None:
    board = Board(checkout, inventory, provider, support)
    try:
        with Live(board.render(), console=console, screen=True) as live:
            while True:
                time.sleep(0.7)
                live.update(board.render())
    except KeyboardInterrupt:
        pass
