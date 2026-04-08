from fastapi import APIRouter

router = APIRouter()
_broker = None


def set_broker(broker):
    global _broker
    _broker = broker


@router.get("/")
def list_positions():
    if not _broker or not _broker.is_connected:
        return []
    result = []
    try:
        # Use cached portfolio items — safe to read from any thread, no ib_insync calls needed
        portfolio = {item.contract.symbol: item for item in _broker.ib.portfolio()}

        for p in _broker.ib.positions():
            qty = int(p.position)
            if qty == 0:
                continue
            symbol   = p.contract.symbol
            side     = "long" if qty > 0 else "short"
            avg_cost = round(float(p.avgCost), 4)

            # Pull market price from cached portfolio item — no API call required
            item = portfolio.get(symbol)
            cur_price = round(float(item.marketPrice), 4) if item and item.marketPrice > 0 else 0

            invested = round(abs(qty) * avg_cost, 2)
            mult     = 1 if side == "long" else -1
            pnl      = round((cur_price - avg_cost) * abs(qty) * mult, 2) if cur_price else None

            result.append({
                "ticker":         symbol,
                "side":           side,
                "qty":            abs(qty),
                "entry_price":    avg_cost,
                "current_price":  cur_price,
                "invested":       invested,
                "unrealised_pnl": pnl,
            })
    except Exception:
        return []
    return result
