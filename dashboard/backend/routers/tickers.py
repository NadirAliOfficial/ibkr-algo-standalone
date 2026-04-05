from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from config.store import ConfigStore
from config.schema import TickerConfig

router = APIRouter()
store = ConfigStore()


class TickerPayload(BaseModel):
    ticker: str
    active: bool = True
    dollar_amount: float = 1000.0
    timeframe: str = "1H"
    indicator_params: dict = {}
    order_type: str = "market"


class BulkEditPayload(BaseModel):
    tickers: List[str]
    dollar_amount: Optional[float] = None
    timeframe: Optional[str] = None
    indicator_params: Optional[dict] = None
    active: Optional[bool] = None


@router.get("/")
def list_tickers():
    return [c.to_dict() for c in store.get_all()]


@router.post("/")
def add_ticker(payload: TickerPayload):
    cfg = TickerConfig(
        ticker=payload.ticker.upper(),
        active=payload.active,
        dollar_amount=payload.dollar_amount,
        timeframe=payload.timeframe,
        indicator_params=payload.indicator_params,
        order_type=payload.order_type,
    )
    store.upsert(cfg)
    return cfg.to_dict()


@router.put("/{ticker}")
def update_ticker(ticker: str, payload: TickerPayload):
    cfg = store.get(ticker.upper())
    if not cfg:
        raise HTTPException(404, f"{ticker} not found")
    cfg.active = payload.active
    cfg.dollar_amount = payload.dollar_amount
    cfg.timeframe = payload.timeframe
    cfg.indicator_params = payload.indicator_params
    cfg.order_type = payload.order_type
    store.upsert(cfg)
    return cfg.to_dict()


@router.delete("/{ticker}")
def remove_ticker(ticker: str):
    cfg = store.get(ticker.upper())
    if not cfg:
        raise HTTPException(404, f"{ticker} not found")
    store.remove(ticker.upper())
    return {"removed": ticker.upper()}


@router.post("/bulk-edit")
def bulk_edit(payload: BulkEditPayload):
    updated = []
    for t in payload.tickers:
        cfg = store.get(t.upper())
        if not cfg:
            continue
        if payload.dollar_amount is not None:
            cfg.dollar_amount = payload.dollar_amount
        if payload.timeframe is not None:
            cfg.timeframe = payload.timeframe
        if payload.indicator_params is not None:
            cfg.indicator_params = payload.indicator_params
        if payload.active is not None:
            cfg.active = payload.active
        store.upsert(cfg)
        updated.append(cfg.to_dict())
    return updated
