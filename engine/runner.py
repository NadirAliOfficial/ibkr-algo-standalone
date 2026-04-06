"""
Main algo engine loop.

Cycle per timeframe candle close:
1. Fetch completed candles from Polygon for each active ticker
2. Run indicator — evaluate on last closed bar
3. Pass any signal to SignalProcessor
4. Log results

Earnings daily scan runs once per day at market open.
Market hours: US Eastern 9:30–16:00, Monday–Friday only.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.data.polygon import PolygonData
from engine.data.earnings import EarningsChecker
from engine.algo.state_machine import PositionStateMachine
from engine.algo.earnings_filter import EarningsFilter
from engine.algo.signal_processor import SignalProcessor
from indicator.base import BaseIndicator
from indicator.erga_indicator import ERGAIndicator

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "3H": 10800,
    "4H": 14400,
    "1D": 86400,
}

# US Eastern offset (approximate: EDT=-4, EST=-5)
def _utc_to_et(utc_dt: datetime) -> datetime:
    offset = -4 if 3 <= utc_dt.month <= 11 else -5
    return utc_dt + timedelta(hours=offset)

def _is_market_hours(utc_now: datetime) -> bool:
    et = _utc_to_et(utc_now)
    if et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = et.time()
    from datetime import time as dtime
    return dtime(9, 30) <= t <= dtime(16, 0)


class AlgoRunner:
    """
    Orchestrates the full algo engine.
    Call register_indicator() for each indicator implementation before start().
    """

    def __init__(self, store: ConfigStore, broker: IBKRConnection):
        self.store = store
        self.broker = broker
        self.polygon = PolygonData()
        self.earnings_checker = EarningsChecker(window_days=3)
        self.state = PositionStateMachine(store, broker)
        self.earnings_filter = EarningsFilter(
            checker=self.earnings_checker,
            store=store,
            broker=broker,
            state=self.state,
            polygon=self.polygon,
            auto_close=True,
        )
        self.processor = SignalProcessor(
            store=store,
            broker=broker,
            state=self.state,
            earnings=self.earnings_filter,
            polygon=self.polygon,
        )

        # ticker -> indicator instance
        self._indicators: Dict[str, BaseIndicator] = {}
        self._running = False
        self._last_eval: Dict[str, float] = {}       # ticker -> last eval unix timestamp
        self._last_timeframe: Dict[str, str] = {}    # ticker -> last known timeframe
        self._last_earnings_scan: Optional[datetime] = None

        # Logs — dashboard reads these
        self.signal_log: list = []
        self.trade_log: list = []
        self.earnings_log: list = []

    def register_indicator(self, ticker: str, indicator: BaseIndicator):
        self._indicators[ticker] = indicator
        logger.info(f"Registered indicator for {ticker}")

    def reinit_indicator(self, ticker: str, indicator: BaseIndicator):
        """Hot-swap indicator instance."""
        self._indicators[ticker] = indicator
        self._last_eval.pop(ticker, None)
        self._last_timeframe.pop(ticker, None)
        logger.info(f"{ticker}: indicator re-initialised")

    def start(self):
        if not self.broker.ensure_connected():
            raise RuntimeError("Cannot connect to IB Gateway")

        self.state.sync_from_ibkr()
        self._running = True
        logger.info("Algo engine started")

        try:
            while self._running:
                self._run_cycle()
                self.broker.sleep(1)
        except KeyboardInterrupt:
            logger.info("Engine stopped by user")
        finally:
            self._running = False
            self.broker.disconnect()

    def stop(self):
        self._running = False

    def _run_cycle(self):
        now = time.time()
        now_dt = datetime.now(timezone.utc)

        # Earnings daily scan — once per day at market open (~14:30 UTC)
        if (
            self._last_earnings_scan is None
            or (now_dt - self._last_earnings_scan).total_seconds() > 86400
        ) and now_dt.hour == 14 and now_dt.minute == 30:
            self.earnings_filter.daily_scan()
            self._last_earnings_scan = now_dt

        # Reconnect check
        if not self.broker.is_connected:
            logger.warning("Connection lost — attempting reconnect")
            if self.broker.reconnect():
                self.state.sync_from_ibkr()

        # Market hours gate — skip signal evaluation outside US market hours
        if not _is_market_hours(now_dt):
            return

        for cfg in self.store.get_active():
            ticker = cfg.ticker
            tf = cfg.timeframe
            interval = TIMEFRAME_SECONDS.get(tf, 3600)
            last = self._last_eval.get(ticker, 0)

            if now - last < interval:
                continue

            self._eval_ticker(ticker, cfg)
            self._last_eval[ticker] = now

    def _eval_ticker(self, ticker: str, cfg):
        indicator = self._indicators.get(ticker)

        # Re-instantiate if timeframe changed (full reset needed)
        prev_tf = self._last_timeframe.get(ticker)
        if prev_tf and prev_tf != cfg.timeframe:
            logger.info(f"{ticker}: timeframe changed {prev_tf} → {cfg.timeframe}, re-instantiating indicator")
            indicator = ERGAIndicator(ticker, cfg.timeframe, cfg.indicator_params)
            self.reinit_indicator(ticker, indicator)

        self._last_timeframe[ticker] = cfg.timeframe

        if not indicator:
            logger.debug(f"{ticker}: no indicator registered — skipping")
            return

        # Hot-reload params if changed
        if indicator.params != cfg.indicator_params:
            indicator.update_params(cfg.indicator_params)

        candles = self.polygon.fetch_candles(ticker, cfg.timeframe)
        if candles is None or candles.empty:
            logger.warning(f"{ticker}: no candles — skipping")
            return

        signal = indicator.evaluate(candles)
        if signal is None:
            return

        result = self.processor.handle(signal)
        self._append_signal_log(result)

        if result["outcome"] == "executed":
            self._append_trade_log(result, cfg)

    def _append_signal_log(self, result: dict):
        self.signal_log.append(result)
        if len(self.signal_log) > 200:
            self.signal_log = self.signal_log[-200:]

    def _append_trade_log(self, result: dict, cfg):
        entry = {**result, "dollar_amount": cfg.dollar_amount, "order_type": cfg.order_type}
        self.trade_log.append(entry)
        if len(self.trade_log) > 200:
            self.trade_log = self.trade_log[-200:]

    def append_earnings_log(self, ticker: str, action: str, earnings_date: str, pnl: float = 0):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "ticker": ticker,
            "earnings_date": earnings_date,
            "action": action,
            "pnl": pnl,
        }
        self.earnings_log.append(entry)
        if len(self.earnings_log) > 100:
            self.earnings_log = self.earnings_log[-100:]
