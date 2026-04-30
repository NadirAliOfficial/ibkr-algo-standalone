from fastapi import APIRouter
from datetime import datetime, timezone, timedelta, time as dtime
from zoneinfo import ZoneInfo

router = APIRouter()

ET = ZoneInfo("America/New_York")
_MARKET_OPEN  = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)

_runner = None
_broker = None
_start_time = datetime.utcnow()


def set_runner(runner):
    global _runner
    _runner = runner


def set_broker(broker):
    global _broker
    _broker = broker


@router.get("/status")
def status():
    connected = _broker.is_connected if _broker else False
    active_count = 0
    halted = []
    account_mode = "unknown"

    if _runner:
        active_count = len(_runner.store.get_active())
        halted = list(_runner.state._halted)

    if _broker and connected:
        try:
            port = _broker.port
            account_mode = "paper" if port == 4002 else "live"
        except Exception:
            account_mode = "unknown"

    return {
        "engine_running": _runner._running if _runner else False,
        "ibkr_connected": connected,
        "account_mode": account_mode,
        "active_tickers": active_count,
        "halted_tickers": halted,
        "uptime_seconds": int((datetime.utcnow() - _start_time).total_seconds()),
        "last_signal": _runner.signal_log[-1] if _runner and _runner.signal_log else None,
    }


@router.get("/market")
def market_status():
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc.astimezone(ET)
    t       = now_et.time()
    is_weekday = now_et.weekday() < 5
    is_open    = is_weekday and _MARKET_OPEN <= t <= _MARKET_CLOSE

    if is_open:
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        secs = int((close_et - now_et).total_seconds())
        label = "closes"
    else:
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        if t >= _MARKET_CLOSE or not is_weekday:
            wd = now_et.weekday()
            days = 3 if (wd == 4 and t >= _MARKET_CLOSE) else (2 if wd == 5 else 1)
            open_et = (now_et + timedelta(days=days)).replace(hour=9, minute=30, second=0, microsecond=0)
        secs = int((open_et - now_et).total_seconds())
        label = "opens"

    h, rem = divmod(max(secs, 0), 3600)
    m, s   = divmod(rem, 60)
    countdown = f"{h}h {m:02d}m" if h > 0 else f"{m}m {s:02d}s"

    return {
        "market_open": is_open,
        "et_time": now_et.strftime("%H:%M:%S"),
        "et_date": now_et.strftime("%b %d, %Y"),
        "next_event": label,
        "countdown": countdown,
    }


@router.post("/clear-halt/{ticker}")
def clear_halt(ticker: str):
    if not _runner:
        return {"ok": False, "reason": "runner not initialised"}
    _runner.state.clear_halt(ticker.upper())
    return {"ok": True, "ticker": ticker.upper()}
