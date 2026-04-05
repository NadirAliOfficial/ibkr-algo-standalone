"""
Entry point — starts the algo engine.

Usage:
  python main.py            # live (port 4001)
  IBKR_PORT=4002 python main.py  # paper

To use: register your indicator below then run.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.runner import AlgoRunner

# ── Register your indicator here ──────────────────────────────────────────────
# from indicator.my_indicator import MyIndicator
#
# def build_indicators(runner, store):
#     for cfg in store.get_all():
#         indicator = MyIndicator(cfg.ticker, cfg.timeframe, cfg.indicator_params)
#         runner.register_indicator(cfg.ticker, indicator)
# ─────────────────────────────────────────────────────────────────────────────


def main():
    store = ConfigStore()
    broker = IBKRConnection(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", 4001)),
        client_id=int(os.getenv("IBKR_CLIENT_ID", 10)),
    )
    runner = AlgoRunner(store=store, broker=broker)

    # Uncomment and plug in your indicator:
    # build_indicators(runner, store)

    runner.start()


if __name__ == "__main__":
    main()
