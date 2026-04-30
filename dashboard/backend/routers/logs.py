import os
from fastapi import APIRouter, Query

router = APIRouter()

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


@router.get("/engine")
def engine_log(lines: int = Query(default=150, le=500)):
    log_path = os.path.join(os.getcwd(), "algo.log")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        result = []
        for line in reversed(tail):
            line = line.rstrip()
            if not line:
                continue
            level = "INFO"
            if " WARNING " in line:
                level = "WARNING"
            elif " ERROR " in line:
                level = "ERROR"
            elif " DEBUG " in line:
                level = "DEBUG"
            result.append({"text": line, "level": level})
        return result
    except Exception:
        return []
