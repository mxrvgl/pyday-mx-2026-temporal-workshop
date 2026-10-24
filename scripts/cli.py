"""The workshop CLI: buy, look things up, break things. It only talks HTTP to the services."""

import os

import httpx
import typer
from rich.console import Console
from rich.table import Table

CHECKOUT = os.getenv("CHECKOUT_URL", "http://localhost:8000")
INVENTORY = os.getenv("INVENTORY_URL", "http://localhost:8001")
PROVIDER = os.getenv("PAYMENT_PROVIDER_URL", "http://localhost:8002")
SUPPORT = os.getenv("SUPPORT_URL", "http://localhost:8003")
SERVICES = {"payments": PROVIDER, "inventory": INVENTORY}

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False)
console = Console(highlight=False)


def request(method: str, url: str, **kwargs):
    try:
        response = httpx.request(method, url, timeout=None, **kwargs)
    except httpx.HTTPError as err:
        console.print(f"[bold red]✗ {err.request.url.host} is unreachable[/] [dim]({type(err).__name__})[/]")
        raise typer.Exit(1)
    if response.status_code >= 400:
        console.print(f"[bold red]✗ {response.status_code}:[/] {response.json().get('detail', response.text)}")
        raise typer.Exit(1)
    return response.json()


def styled(status: str) -> str:
    color = "green" if status == "COMPLETED" else "cyan" if status == "PENDING" else "yellow" if status in ("DECLINED", "REFUNDED") else "red"
    return f"[bold {color}]{status}[/]"


@app.command()
def buy(order_id: str = typer.Option("", "--id"), customer: str = "alice", sku: str = "TSHIRT-M"):
    """Place an order and wait for checkout's answer."""
    with console.status(f"[cyan]{customer} is buying {sku} · {order_id}[/]"):
        result = request("POST", f"{CHECKOUT}/orders", json={"order_id": order_id, "customer_id": customer, "sku": sku})
    console.print(f"[bold]{result['order_id']}[/]  {styled(result['status'])}")


@app.command()
def status(order_id: str):
    """What each service knows about one order."""
    order = request("GET", f"{CHECKOUT}/orders/{order_id}")
    reservation = httpx.get(f"{INVENTORY}/reservations/{order_id}").json()
    charges = request("GET", f"{PROVIDER}/v1/ledger", params={"order_id": order_id})
    console.print(f"[bold]{order_id}[/]")
    console.print(f"  checkout   {styled(order['status'])}")
    console.print(f"  inventory  {reservation.get('status', 'nothing reserved')}")
    for c in charges:
        replays = f"  [yellow](+{c['replays']} duplicate requests absorbed)[/]" if c["replays"] else ""
        console.print(f"  payments   {c['operation']} MXN {c['amount'] / 100:,.2f} {c['result']}{replays}")
    if not charges:
        console.print("  payments   no money moved")


@app.command()
def stock():
    """Stock per product, and the latest reservations."""
    table = Table("sku", "in stock")
    for p in request("GET", f"{INVENTORY}/products"):
        table.add_row(p["sku"], str(p["stock"]))
    console.print(table)
    table = Table("order", "sku", "status")
    for r in request("GET", f"{INVENTORY}/reservations"):
        table.add_row(r["order_id"], r["sku"], r["status"])
    console.print(table)


@app.command()
def ledger(order_id: str = typer.Argument("")):
    """Every operation the payment provider recorded."""
    table = Table("time", "order", "operation", "amount", "result", "replays")
    for e in request("GET", f"{PROVIDER}/v1/ledger", params={"order_id": order_id}):
        table.add_row(e["time"], e["order_id"], e["operation"], f"MXN {e['amount'] / 100:,.2f}", e["result"], str(e["replays"]))
    console.print(table)


@app.command()
def fault(service: str, down: bool = False, latency_s: float = 0, fail_next: int = 0, refuse_shipping: bool = False):
    """Make an external service misbehave. No options: back to normal."""
    params = {"down": down, "latency_s": latency_s, "fail_next": fail_next}
    if service == "inventory":
        params["refuse_shipping"] = refuse_shipping
    state = request("POST", f"{SERVICES[service]}/faults", params=params)
    on = [k for k, v in state.items() if v]
    console.print(f"{service}: " + (f"[bold red]{', '.join(on)}[/]" if on else "[green]normal[/]"))


@app.command()
def chat(order_id: str):
    """Chat with support about one order. An empty line ends the chat."""
    console.print(f"[dim]Chatting with support about {order_id}. An empty line ends the chat.[/]")
    while True:
        try:
            text = console.input("[bold]you ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        try:
            with console.status("[cyan]support is typing…[/]"):
                replies = request("POST", f"{SUPPORT}/chats/{order_id}/messages", json={"text": text})
        except typer.Exit:
            continue  # the error is printed; the customer can try again
        for reply in replies:
            if "tool" in reply:
                console.print(f"  [dim]⚙ {reply['tool']}[/]")
            else:
                console.print(f"[bold green]support ›[/] {reply['agent']}")


@app.command()
def board():
    """Live board: services, faults, the last order. Ctrl-C to quit."""
    from board import run

    run(console, CHECKOUT, INVENTORY, PROVIDER, SUPPORT)


if __name__ == "__main__":
    app()
