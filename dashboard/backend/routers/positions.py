from fastapi import APIRouter
from engine.broker.connection import IBKRConnection
import os

router = APIRouter()

_broker = None

def get_broker() -> IBKRConnection:
    global _broker
    if _broker is None:
        _broker = IBKRConnection()
        _broker.connect()
    return _broker


@router.get("/")
def list_positions():
    broker = get_broker()
    if not broker.is_connected:
        return {"error": "not connected to IB Gateway", "positions": []}

    raw = broker.get_positions()
    result = []
    for ticker, pos in raw.items():
        bid, ask = broker.get_bid_ask(ticker)
        current_price = ask or bid or 0
        entry_price = 0  # IBKR avgCost available via ib.positions()
        result.append({
            "ticker": ticker,
            "side": pos["side"],
            "qty": pos["qty"],
            "current_price": current_price,
        })
    return result
