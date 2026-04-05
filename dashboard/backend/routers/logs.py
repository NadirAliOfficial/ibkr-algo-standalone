from fastapi import APIRouter

router = APIRouter()

# Injected by main app after runner starts
_runner = None

def set_runner(runner):
    global _runner
    _runner = runner


@router.get("/signals")
def signal_log():
    if not _runner:
        return []
    return list(reversed(_runner.signal_log[-50:]))


@router.get("/trades")
def trade_log():
    if not _runner:
        return []
    return list(reversed(_runner.trade_log[-100:]))


@router.get("/earnings")
def earnings_log():
    if not _runner:
        return []
    return list(reversed(_runner.earnings_log[-100:]))
