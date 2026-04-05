"""
Entry point — ERGA-SS Autonomous Algo Engine

Usage:
  python main.py            # paper (port 4002 from .env)
  IBKR_PORT=4001 python main.py  # live

Tickers are loaded from config/tickers.json (managed via dashboard or manually).
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("algo.log"),
    ],
)

from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.runner import AlgoRunner
from indicator.erga_indicator import ERGAIndicator


def build_indicators(runner: AlgoRunner, store: ConfigStore):
    for cfg in store.get_all():
        ind = ERGAIndicator(
            ticker=cfg.ticker,
            timeframe=cfg.timeframe,
            params=cfg.indicator_params,
        )
        runner.register_indicator(cfg.ticker, ind)


def main():
    store = ConfigStore()
    broker = IBKRConnection(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", 4002)),
        client_id=int(os.getenv("IBKR_CLIENT_ID", 10)),
    )
    runner = AlgoRunner(store=store, broker=broker)
    build_indicators(runner, store)

    logging.getLogger(__name__).info(
        f"Starting ERGA-SS engine | "
        f"port={os.getenv('IBKR_PORT', 4002)} | "
        f"tickers={len(store.get_all())}"
    )
    runner.start()


if __name__ == "__main__":
    main()
