"""
ERGA-SS Autonomous Algo Engine — entry point.

Launches two subsystems in a single process:
  1. FastAPI dashboard (background thread, port 8000)
  2. Algo engine loop (main thread)

Usage:
  python main.py                     # paper (port 4002 from .env)
  IBKR_PORT=4001 python main.py      # live
  IBKR_PORT=4001 LOG_LEVEL=DEBUG python main.py
"""

import logging
import os
import threading
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("algo.log"),
    ],
)
logger = logging.getLogger(__name__)


def _start_dashboard(app, host: str, port: int):
    """Run FastAPI in a daemon thread so it dies cleanly when the main thread exits."""
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def main():
    from config.store import ConfigStore
    from engine.broker.connection import IBKRConnection
    from engine.runner import AlgoRunner
    from indicator.erga_indicator import ERGAIndicator
    from dashboard.backend.main import app
    from dashboard.backend.routers import logs, system, positions

    # ── config & broker ───────────────────────────────────────────────────────
    store = ConfigStore()
    broker = IBKRConnection(
        host=os.getenv("IBKR_HOST",      "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT",  4002)),
        client_id=int(os.getenv("IBKR_CLIENT_ID", 10)),
    )

    # ── runner ────────────────────────────────────────────────────────────────
    runner = AlgoRunner(store=store, broker=broker)

    # Register one indicator per configured ticker
    for cfg in store.get_all():
        ind = ERGAIndicator(
            ticker=cfg.ticker,
            timeframe=cfg.timeframe,
            params=cfg.indicator_params,
        )
        runner.register_indicator(cfg.ticker, ind)

    logger.info(
        f"Engine initialised | "
        f"port={broker.port} | "
        f"mode={'PAPER' if broker.port == 4002 else 'LIVE'} | "
        f"tickers={len(store.get_all())}"
    )

    # ── wire runner/broker into dashboard routers ─────────────────────────────
    logs.set_runner(runner)
    system.set_runner(runner)
    system.set_broker(broker)
    positions.set_broker(broker)

    # ── launch dashboard in background thread ─────────────────────────────────
    dash_host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    dash_port = int(os.getenv("DASHBOARD_PORT", 8000))
    dash_thread = threading.Thread(
        target=_start_dashboard,
        args=(app, dash_host, dash_port),
        daemon=True,
        name="dashboard",
    )
    dash_thread.start()
    logger.info(f"Dashboard running on http://{dash_host}:{dash_port}")

    # ── start algo engine (blocking) ──────────────────────────────────────────
    runner.start()


if __name__ == "__main__":
    main()
