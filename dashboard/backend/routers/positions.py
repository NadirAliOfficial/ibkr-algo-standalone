from fastapi import APIRouter
from engine.broker.connection import IBKRConnection

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
        return []

    result = []
    try:
        for p in broker.ib.positions():
            symbol = p.contract.symbol
            qty = int(p.position)
            if qty == 0:
                continue
            side = "long" if qty > 0 else "short"
            avg_cost = round(float(p.avgCost), 2)
            bid, ask = broker.get_bid_ask(symbol)
            current_price = round(ask or bid or 0, 2)
            invested = round(abs(qty) * avg_cost, 2)
            unrealised_pnl = round((current_price - avg_cost) * abs(qty) * (1 if side == "long" else -1), 2)
            result.append({
                "ticker": symbol,
                "side": side,
                "qty": abs(qty),
                "entry_price": avg_cost,
                "current_price": current_price,
                "invested": invested,
                "unrealised_pnl": unrealised_pnl,
            })
    except Exception as e:
        return []
    return result
