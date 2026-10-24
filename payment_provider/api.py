"""A mock payment provider, in the style of Stripe. It stands in for a third party:
you call it, you don't change it.

  POST /v1/charges   charge a payment method. pm_card_visa works, pm_card_chargeDeclined doesn't
  POST /v1/refunds   refund a charge
  GET  /v1/ledger    every operation it accepted or declined

Both POSTs take an Idempotency-Key header: a repeated key gets the original
answer back, and nothing happens twice. The ledger counts those replays.

POST /faults makes it misbehave on purpose: `make payments-down`, `make payments-flaky`...
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="Payment provider (mock)")

DECLINING = {"pm_card_chargeDeclined", "pm_card_insufficientFunds"}
STATE_FILE = Path("/tmp/provider.json")  # survives a restart of the process, not of the container

books = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"charges": {}, "ledger": [], "answers": {}}
faults = {"down": False, "latency_s": 0.0, "fail_next": 0}


def save() -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(books))
    tmp.replace(STATE_FILE)


async def misbehave() -> None:
    """Applied to every /v1 call, before anything else happens."""
    if faults["latency_s"]:
        await asyncio.sleep(faults["latency_s"])
    if faults["down"]:
        raise HTTPException(503, "payment provider is down")
    if faults["fail_next"] > 0:
        faults["fail_next"] -= 1
        raise HTTPException(503, "payment provider hiccup")


def replay(key: str) -> dict | None:
    """Seen this Idempotency-Key before? Then answer what we answered then."""
    if key not in books["answers"]:
        return None
    for entry in books["ledger"]:
        if entry["idempotency_key"] == key:
            entry["replays"] += 1
    save()
    log.info("replay    %s", key)
    return books["answers"][key]


def record(key: str, operation: str, order_id: str, amount: int, result: str, answer: dict | None) -> None:
    books["ledger"].append({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "operation": operation,
        "order_id": order_id,
        "amount": amount,
        "result": result,
        "idempotency_key": key,
        "replays": 0,
    })
    if answer is not None:
        books["answers"][key] = answer
    save()
    log.info("%-9s %s  %s", operation, order_id, result)


class ChargeRequest(BaseModel):
    amount: int  # in cents
    currency: str = "mxn"
    payment_method: str
    order_id: str


class RefundRequest(BaseModel):
    charge: str


@app.post("/v1/charges", dependencies=[Depends(misbehave)])
async def charge(req: ChargeRequest, idempotency_key: str = Header()):
    if (answer := replay(idempotency_key)) is not None:
        return answer
    if req.payment_method in DECLINING:
        record(idempotency_key, "charge", req.order_id, req.amount, "DECLINED", None)
        raise HTTPException(402, "card_declined")
    charge_id = f"ch_{uuid.uuid4().hex[:12]}"
    books["charges"][charge_id] = {"order_id": req.order_id, "amount": req.amount, "refunded": False}
    answer = {"id": charge_id, "status": "succeeded", "amount": req.amount, "order_id": req.order_id}
    record(idempotency_key, "charge", req.order_id, req.amount, "OK", answer)
    return answer


@app.post("/v1/refunds", dependencies=[Depends(misbehave)])
async def refund(req: RefundRequest, idempotency_key: str = Header()):
    if (answer := replay(idempotency_key)) is not None:
        return answer
    charge = books["charges"].get(req.charge)
    if charge is None:
        raise HTTPException(404, f"no such charge {req.charge}")
    if charge["refunded"]:
        raise HTTPException(409, f"{req.charge} was already refunded")
    charge["refunded"] = True
    answer = {"id": f"re_{uuid.uuid4().hex[:12]}", "charge": req.charge, "status": "succeeded"}
    record(idempotency_key, "refund", charge["order_id"], charge["amount"], "OK", answer)
    return answer


@app.get("/v1/ledger")
async def ledger(order_id: str = ""):
    return [e for e in books["ledger"] if order_id in ("", e["order_id"])]


@app.get("/faults")
async def get_faults():
    return faults


@app.post("/faults")
async def set_faults(down: bool = False, latency_s: float = 0, fail_next: int = 0):
    faults.update(down=down, latency_s=latency_s, fail_next=fail_next)
    log.info("faults    %s", faults)
    return faults


@app.get("/health")
async def health():
    return {"ok": True}
