from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

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
