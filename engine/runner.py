"""
ERGA-SS Algo Engine — main execution loop.

Candle-close aligned evaluation:
  Bars are evaluated within 30 seconds of their expected close time (ET session-aligned).
  This minimises signal latency while guaranteeing we only act on completed bars.

Cycle (runs every 15 seconds):
  1. Reconnect check
  2. Earnings daily scan (once per day, 09:35 ET)
  3. Market hours gate
  4. For each active ticker: check if a new bar has closed → evaluate

Hot-reload:
  - Config changes (params, timeframe, dollar_amount) take effect on the next cycle
  - Timeframe change → indicator instance is reset
  - Param change → params applied in-place without reset
"""

import logging
import time
import math
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.data.polygon import PolygonData
from engine.data.earnings import EarningsChecker
from engine.algo.state_machine import PositionStateMachine
from engine.algo.earnings_filter import EarningsFilter
from engine.algo.signal_processor import SignalProcessor
from engine.log_store import LogStore
from indicator.base import BaseIndicator
from indicator.erga_indicator import ERGAIndicator

logger = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc

CYCLE_SLEEP = 1  # seconds between main loop iterations (must stay ≤1 for ib_insync event loop)

TIMEFRAME_MINUTES: Dict[str, int] = {
    "1m":  1,
    "5m":  5,
    "15m": 15,
    "30m": 30,
    "1H":  60,
    "2H":  120,
    "3H":  180,
    "4H":  240,
    "1D":  1440,
}

MARKET_OPEN  = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
EARNINGS_SCAN_HOUR   = 9
EARNINGS_SCAN_MINUTE = 35

LOG_MAX_SIGNALS  = 500
LOG_MAX_TRADES   = 500
LOG_MAX_EARNINGS = 200


def _now_et() -> datetime:
    return datetime.now(UTC).astimezone(ET)


def _is_market_hours(now_et: Optional[datetime] = None) -> bool:
    et = now_et or _now_et()
    if et.weekday() >= 5:
        return False
    return MARKET_OPEN <= et.time() <= MARKET_CLOSE


def _next_bar_close_utc(timeframe: str, now_utc: Optional[datetime] = None) -> Optional[datetime]:
    """
    Return the UTC timestamp of the next expected bar close for the given timeframe,
    aligned to ET session open (09:30 ET).
    Returns None for daily bars (evaluated once per day, handled separately).
    """
    if timeframe == "1D":
        return None

    mins = TIMEFRAME_MINUTES.get(timeframe, 60)
    now  = now_utc or datetime.now(UTC)
    et   = now.astimezone(ET)

    session_open_et = et.replace(hour=9, minute=30, second=0, microsecond=0)
    delta_mins = (et - session_open_et).total_seconds() / 60

    if delta_mins < 0:
        # Before session open today — first close is session_open + mins
        next_close_et = session_open_et + timedelta(minutes=mins)
    else:
        # How many complete bars have elapsed since session open?
        bars_elapsed  = math.floor(delta_mins / mins)
        next_close_et = session_open_et + timedelta(minutes=(bars_elapsed + 1) * mins)

    return next_close_et.astimezone(UTC)


class AlgoRunner:

    def __init__(self, store: ConfigStore, broker: IBKRConnection):
        self.store   = store
        self.broker  = broker
        self.polygon = PolygonData()

        earnings_checker = EarningsChecker(window_days=3)
        self.state    = PositionStateMachine(store, broker)
        self.earnings = EarningsFilter(
            checker=earnings_checker,
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
            earnings=self.earnings,
            polygon=self.polygon,
        )

        self._indicators:    Dict[str, BaseIndicator] = {}
        self._last_timeframe: Dict[str, str]          = {}
        self._last_params:    Dict[str, dict]         = {}
        # next_eval[ticker] = next expected bar close UTC (+ buffer)
        self._next_eval:      Dict[str, datetime]     = {}

        self._running               = False
        self._last_earnings_scan_et: Optional[datetime] = None

        # shared logs — read by dashboard API (Redis-backed with in-memory fallback)
        self._signal_store   = LogStore("signal")
        self._trade_store    = LogStore("trade")
        self._earnings_store = LogStore("earnings")

        self.signal_log   = self._signal_store._mem
        self.trade_log    = self._trade_store._mem
        self.earnings_log = self._earnings_store._mem

    # ── indicator registration ────────────────────────────────────────────────

    def register_indicator(self, ticker: str, indicator: BaseIndicator):
        self._indicators[ticker]     = indicator
        self._last_timeframe[ticker] = indicator.timeframe
        self._last_params[ticker]    = dict(indicator.params)
        logger.info(f"Registered indicator for {ticker}")

    def _reset_indicator(self, ticker: str, cfg) -> BaseIndicator:
        ind = ERGAIndicator(ticker, cfg.timeframe, cfg.indicator_params)
        self._indicators[ticker]     = ind
        self._last_timeframe[ticker] = cfg.timeframe
        self._last_params[ticker]    = dict(cfg.indicator_params)
        self._next_eval.pop(ticker, None)
        logger.info(f"{ticker}: indicator reset (timeframe={cfg.timeframe})")
        return ind

    # ── main loop ─────────────────────────────────────────────────────────────

    def start(self):
        if not self.broker.ensure_connected():
            raise RuntimeError("Cannot connect to IB Gateway — check that IB Gateway is running")

        logger.info(
            f"ERGA-SS engine starting | "
            f"port={self.broker.port} | "
            f"mode={'PAPER' if self.broker.port == 4002 else 'LIVE'} | "
            f"tickers={len(self.store.get_all())}"
        )

        # Rebuild position state from IBKR before processing any signals
        self.state.sync_from_ibkr()

        self._running = True
        try:
            while self._running:
                try:
                    self._run_cycle()
                except Exception as e:
                    logger.exception(f"Unhandled error in run cycle: {e}")
                self.broker.sleep(CYCLE_SLEEP)
        except KeyboardInterrupt:
            logger.info("Engine stopped by user")
        finally:
            self._running = False
            self.broker.disconnect()
            logger.info("Engine stopped")

    def stop(self):
        self._running = False

    def _run_cycle(self):
        now_utc = datetime.now(UTC)
        now_et  = now_utc.astimezone(ET)

        # Reconnect if dropped
        if not self.broker.is_connected:
            logger.warning("Connection lost — reconnecting")
            if self.broker.reconnect():
                self.state.sync_from_ibkr()
            else:
                return  # Skip cycle if reconnect failed

        # Earnings daily scan — once per day, 09:35 ET
        self._maybe_run_earnings_scan(now_et)

        if not _is_market_hours(now_et):
            return

        for cfg in self.store.get_active():
            try:
                self._eval_ticker(cfg, now_utc)
            except Exception as e:
                logger.exception(f"{cfg.ticker}: unexpected error in eval: {e}")

    # ── earnings scan ─────────────────────────────────────────────────────────

    def _maybe_run_earnings_scan(self, now_et: datetime):
        if now_et.weekday() >= 5:
            return
        scan_time = dtime(EARNINGS_SCAN_HOUR, EARNINGS_SCAN_MINUTE)
        if now_et.time() < scan_time:
            return
        today = now_et.date()
        if self._last_earnings_scan_et and self._last_earnings_scan_et.date() == today:
            return

        self.earnings.daily_scan()
        self._last_earnings_scan_et = now_et

    # ── per-ticker evaluation ─────────────────────────────────────────────────

    def _eval_ticker(self, cfg, now_utc: datetime):
        ticker = cfg.ticker
        tf     = cfg.timeframe

        # ── hot-reload: timeframe change → full reset ─────────────────────────
        if self._last_timeframe.get(ticker) != tf:
            self._reset_indicator(ticker, cfg)

        ind = self._indicators.get(ticker)
        if ind is None:
            ind = self._reset_indicator(ticker, cfg)

        # ── hot-reload: params change → in-place update ───────────────────────
        if cfg.indicator_params != self._last_params.get(ticker):
            ind.update_params(cfg.indicator_params)
            self._last_params[ticker] = dict(cfg.indicator_params)
            logger.info(f"{ticker}: indicator params hot-reloaded")

        # ── candle-close aligned timing ───────────────────────────────────────
        if tf == "1D":
            # Daily: evaluate once per day at market open + buffer
            et = now_utc.astimezone(ET)
            today = et.date()
            last = self._next_eval.get(ticker)
            if last and last.astimezone(ET).date() == today:
                return  # already evaluated today
            if et.time() < dtime(9, 35):
                return  # wait for daily bar to be confirmed
        else:
            # Intraday: evaluate within 30s of each bar close
            next_close = _next_bar_close_utc(tf, now_utc)
            eval_at    = next_close + timedelta(seconds=30)
            last       = self._next_eval.get(ticker)
            if now_utc < eval_at:
                return  # bar hasn't closed yet
            if last and last >= eval_at:
                return  # already evaluated this bar

        # ── fetch + evaluate ──────────────────────────────────────────────────
        candles = self.polygon.fetch_candles(ticker, tf)
        if candles is None or candles.empty:
            logger.warning(f"{ticker}: no candles returned — skipping")
            return

        signal = ind.evaluate(candles)

        # Mark evaluated for this bar
        if tf == "1D":
            self._next_eval[ticker] = now_utc
        else:
            self._next_eval[ticker] = _next_bar_close_utc(tf, now_utc) + timedelta(seconds=30)

        if signal is None:
            return

        result = self.processor.handle(signal)
        self._log_signal(result, cfg)

    # ── logging ───────────────────────────────────────────────────────────────

    def _log_signal(self, result: dict, cfg):
        entry = {
            **result,
            "timeframe":     cfg.timeframe,
            "dollar_amount": cfg.dollar_amount,
            "order_type":    cfg.order_type,
        }
        self._signal_store.append(entry)
        self.signal_log = self._signal_store._mem

        if result.get("outcome") == "executed":
            self._trade_store.append(entry)
            self.trade_log = self._trade_store._mem

    def append_earnings_log(self, ticker: str, action: str, earnings_date: str, pnl: float = 0.0):
        entry = {
            "timestamp":     datetime.now(UTC).isoformat(),
            "ticker":        ticker,
            "earnings_date": earnings_date,
            "action":        action,
            "pnl":           pnl,
        }
        self._earnings_store.append(entry)
        self.earnings_log = self._earnings_store._mem
