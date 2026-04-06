# =============================================================================
# erga_standalone.py  —  ERGA-SS Standalone Trading System
# Direct IBKR connection via ib_insync + live Streamlit dashboard
#
# OVERVIEW
# --------
# This is an alternative to the QuantConnect version. It runs entirely on your
# own machine or server and connects directly to Interactive Brokers via
# ib_insync. It does NOT require a QuantConnect subscription.
#
# ARCHITECTURE
# ------------
#   erga_ss_lib.py        — ERGA-SS indicator (shared with QC version)
#   erga_standalone.py    — this file: IBKR connection, signal engine, scheduler
#   dashboard.py          — Streamlit web UI (run separately in a second terminal)
#   state.json            — live state file read by the dashboard (auto-created)
#
# INSTALLATION
# ------------
#   pip install ib_insync pandas apscheduler streamlit
#
#   Also requires: TWS (Trader Workstation) or IB Gateway running locally
#   with API connections enabled on port 7497 (paper) or 7496 (live).
#
# HOW TO RUN
# ----------
#   Terminal 1 — trading engine:
#     python erga_standalone.py
#
#   Terminal 2 — live dashboard:
#     streamlit run dashboard.py
#
# CONFIGURATION
# -------------
#   Edit the CONFIG section below before running.
#
# WATCHLIST FORMAT
# ----------------
#   Edit WATCHLIST at the bottom of this file, or replace with CSV loading.
#   Each entry is a dict with the same keys as the Google Sheets columns.
#
# LOG TAGS
#   ADD    — symbol added to watchlist
#   SIGNAL — ERGA-SS flip detected
#   CONF   — confirmation bar met, order submitted
#   BLOCK  — signal blocked (reason shown)
#   FILL   — order filled
#   AUDIT  — periodic position summary
#   ERROR  — exception caught
# =============================================================================

import json
import math
import time
import logging
import os
from datetime import datetime, timedelta, time as dtime
from math import floor
from collections import deque

import pandas as pd
from ib_insync import IB, Stock, MarketOrder, util
from apscheduler.schedulers.background import BackgroundScheduler

from erga_ss_lib import ERGA_SS, get_confirm_bars, get_wait_minutes, parse_calc_mode

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG = {
    # IBKR connection
    "host":           "127.0.0.1",
    "port":           7497,       # 7497 = paper trading, 7496 = live
    "client_id":      1,
    "is_live":        False,      # set True when trading real money

    # Market session (US Eastern, converted to UTC at runtime)
    "market_open":    dtime(9, 45),    # 9:45 AM ET (15 min gate already built-in)
    "market_close":   dtime(15, 50),   # 3:50 PM ET

    # Portfolio limits
    "max_positions":        20,
    "max_exposure_dollars": 500_000,

    # Entry filters
    "gap_filter_pct":    3.0,    # block if overnight gap > this %
    "atr_rel_min":       0.005,  # block if ATR/price < this
    "atr_hist_mult":     2.0,    # block if current ATR > this * 100-bar ATR avg
    "adx_min":           15.0,   # block if ADX < this

    # State file for dashboard
    "state_file": "state.json",

    # Logging
    "log_file": "erga_trading.log",
}

# ERGA-SS defaults applied when a watchlist entry doesn't override
ERGA_DEFAULTS = {
    "slow_len":       50,
    "fast_len":       15,
    "er_len":         20,
    "pre_len":        5,
    "calc_mode":      "Adaptive",
    "min_quality":    25.0,
    "hysteresis":     0.1,
    "buffer_mult":    0.5,
    "min_trend_bars": 3,
    "use_vol_scale":  False,
    "vol_clamp":      0.30,
}

# =============================================================================
# WATCHLIST
# Edit this list to define which symbols to trade.
# For each symbol, only 'symbol', 'timeframe', and 'dollar' are required.
# All erga.* keys are optional — blank/missing uses ERGA_DEFAULTS.
# =============================================================================

WATCHLIST = [
    # Example entries — replace with your 50 stocks
    # Trending ETFs — use Adaptive, standard lengths
    {"symbol": "GDX",  "active": 1, "timeframe": "60m",  "dollar": 5000, "erga.calc_mode": "Adaptive"},
    {"symbol": "GLD",  "active": 1, "timeframe": "60m",  "dollar": 5000, "erga.calc_mode": "Adaptive"},
    {"symbol": "XLE",  "active": 1, "timeframe": "60m",  "dollar": 5000, "erga.calc_mode": "Adaptive"},

    # Large-cap growth — Adaptive, standard or slightly scaled
    {"symbol": "AAPL", "active": 1, "timeframe": "60m",  "dollar": 5000, "erga.calc_mode": "Adaptive"},
    {"symbol": "MSFT", "active": 1, "timeframe": "60m",  "dollar": 5000, "erga.calc_mode": "Adaptive", "erga.er_scale": "1.2"},

    # High-beta momentum — Manual Fast or Adaptive with scale 0.6-0.7
    {"symbol": "NVDA", "active": 1, "timeframe": "15m",  "dollar": 5000, "erga.calc_mode": "Manual Fast", "erga.er_scale": "0.6"},
    {"symbol": "TSLA", "active": 1, "timeframe": "30m",  "dollar": 5000, "erga.calc_mode": "Manual Fast", "erga.er_scale": "0.6"},
    {"symbol": "AMD",  "active": 1, "timeframe": "30m",  "dollar": 5000, "erga.calc_mode": "Adaptive",    "erga.er_scale": "0.7"},

    # Slow defensives — Manual Slow, scale 1.4
    {"symbol": "JNJ",  "active": 1, "timeframe": "120m", "dollar": 5000, "erga.calc_mode": "Manual Slow", "erga.er_scale": "1.4"},
    {"symbol": "KO",   "active": 1, "timeframe": "120m", "dollar": 5000, "erga.calc_mode": "Manual Slow", "erga.er_scale": "1.4"},
]

# =============================================================================
# Logging setup
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("erga")


# =============================================================================
# SymbolState
# =============================================================================

class SymbolState:
    def __init__(self, entry: dict):
        self.ticker     = entry["symbol"].upper()
        self.active     = int(entry.get("active", 1)) == 1
        self.dollar     = float(entry.get("dollar", 5000))
        self.tf_minutes = self._parse_tf(str(entry.get("timeframe", "60m")))

        # Confirmation / wait gates (automatic from timeframe)
        self.confirm_bars  = get_confirm_bars(self.tf_minutes)
        self.wait_minutes  = get_wait_minutes(self.tf_minutes)
        self.pending_trend = 0
        self.confirm_count = 0

        # Build ERGA params
        params = dict(ERGA_DEFAULTS)
        er_scale = float(entry.get("erga.er_scale", 0) or 0)
        if er_scale > 0:
            params["slow_len"] = max(10, round(ERGA_DEFAULTS["slow_len"] * er_scale))
            params["fast_len"] = max(3,  round(ERGA_DEFAULTS["fast_len"] * er_scale))
        for key in ("slow_len", "fast_len", "er_len", "min_quality"):
            full_key = f"erga.{key}"
            if entry.get(full_key, ""):
                params[key] = float(entry[full_key]) if key == "min_quality" else int(entry[full_key])
        if entry.get("erga.calc_mode", ""):
            params["calc_mode"] = parse_calc_mode(entry["erga.calc_mode"])

        self.erga = ERGA_SS(**{k: v for k, v in params.items()})

        # Bar buffer — stores recent OHLC for the target timeframe
        self._open_bar  = None   # accumulating bar
        self._bar_start = None   # UTC timestamp of current bar bucket

        # Current position tracked locally (verified against IBKR on each cycle)
        self.position_qty   = 0   # positive = long, negative = short
        self.last_fill_price = 0.0
        self.last_signal    = "NONE"
        self.bars_processed = 0

    @staticmethod
    def _parse_tf(tf_str: str) -> int:
        """Returns timeframe in minutes."""
        tf_str = tf_str.strip().lower()
        if tf_str.endswith("h"):
            return int(tf_str[:-1]) * 60
        if tf_str.endswith("m"):
            return int(tf_str[:-1])
        return 60


# =============================================================================
# Trading Engine
# =============================================================================

class ERGAEngine:

    def __init__(self):
        self.ib      = IB()
        self.states  = {}    # ticker → SymbolState
        self._bar_cache: dict[str, list] = {}   # ticker → list of 1-min bars (rolling buffer)

        # Build states from WATCHLIST
        for entry in WATCHLIST:
            ticker = entry["symbol"].upper()
            st     = SymbolState(entry)
            if st.active:
                self.states[ticker] = st
                self._bar_cache[ticker] = []
                log.info(f"ADD|{ticker}|{st.tf_minutes}m|${st.dollar:.0f}|"
                         f"{st.erga.calc_mode}|slow={st.erga.slow_len}|fast={st.erga.fast_len}|"
                         f"confirm={st.confirm_bars}|wait={st.wait_minutes}min")

    # -------------------------------------------------------------------------
    # IBKR connection
    # -------------------------------------------------------------------------

    def connect(self):
        self.ib.connect(CONFIG["host"], CONFIG["port"], clientId=CONFIG["client_id"])
        log.info(f"Connected to IBKR | port={CONFIG['port']} | live={CONFIG['is_live']}")

        # Subscribe to 1-min real-time bars for each symbol
        for ticker, st in self.states.items():
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            bars = self.ib.reqRealTimeBars(contract, 5, "TRADES", False)
            bars.updateEvent += lambda bars, has_new, t=ticker: self._on_realtime_bar(t, bars)
            st._ib_contract = contract
            st._realtime_bars = bars

        log.info(f"Subscribed to real-time bars for {len(self.states)} symbols")

    def disconnect(self):
        self.ib.disconnect()
        log.info("Disconnected from IBKR")

    # -------------------------------------------------------------------------
    # Bar handler — called every 5 seconds by ib_insync
    # -------------------------------------------------------------------------

    def _on_realtime_bar(self, ticker: str, bars):
        """Accumulates 5-second IBKR real-time bars into target-TF bars."""
        if not bars or ticker not in self.states:
            return
        st  = self.states[ticker]
        bar = bars[-1]   # most recent 5-second bar

        now = datetime.utcnow()
        if not self._is_market_hours(now):
            return

        # Accumulate into 1-minute buckets first
        bucket_minute = now.replace(second=0, microsecond=0)
        cache = self._bar_cache.setdefault(ticker, [])

        if not cache or cache[-1]["ts"] != bucket_minute:
            cache.append({
                "ts":    bucket_minute,
                "open":  float(bar.open),
                "high":  float(bar.high),
                "low":   float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            })
        else:
            m = cache[-1]
            m["high"]  = max(m["high"],  float(bar.high))
            m["low"]   = min(m["low"],   float(bar.low))
            m["close"] = float(bar.close)
            m["volume"] += float(bar.volume)

        # Keep rolling 500 minutes max
        if len(cache) > 500:
            cache.pop(0)

        # Check if we've completed a full target-TF bar
        self._try_complete_tf_bar(ticker, now)

    def _try_complete_tf_bar(self, ticker: str, now: datetime):
        """Checks if enough 1-min bars have accumulated to form a target-TF bar."""
        st    = self.states[ticker]
        cache = self._bar_cache.get(ticker, [])

        if len(cache) < st.tf_minutes:
            return

        # Group the last N 1-minute bars into a single TF bar
        segment = cache[-st.tf_minutes:]
        # Only fire if the last bar's timestamp just ticked over
        bar_close_minute = segment[-1]["ts"]
        if st._bar_start == bar_close_minute:
            return
        st._bar_start = bar_close_minute

        tf_bar = {
            "open":   segment[0]["open"],
            "high":   max(b["high"]  for b in segment),
            "low":    min(b["low"]   for b in segment),
            "close":  segment[-1]["close"],
        }

        self._process_tf_bar(ticker, tf_bar)

    # -------------------------------------------------------------------------
    # TF bar processing
    # -------------------------------------------------------------------------

    def _process_tf_bar(self, ticker: str, bar: dict):
        """Runs ERGA-SS on a completed TF bar and manages confirmation logic."""
        st = self.states[ticker]
        st.bars_processed += 1

        st.erga.update(bar["close"], bar["high"], bar["low"])
        desired = st.erga.trend

        # Current IBKR position
        current_direction = self._current_direction(ticker)

        if desired != current_direction:
            if st.pending_trend != desired:
                st.pending_trend  = desired
                st.confirm_count  = 1
                log.info(f"SIGNAL|{ticker}|{'LONG' if desired==1 else 'SHORT'}|"
                         f"ER={st.erga.er:.2f}|Q={st.erga.quality:.0f}|"
                         f"len={st.erga.active_len}|confirm=1/{st.confirm_bars}")
            else:
                st.confirm_count += 1
                log.info(f"SIGNAL|{ticker}|{'LONG' if desired==1 else 'SHORT'}|"
                         f"confirm={st.confirm_count}/{st.confirm_bars}")

            if st.confirm_count >= st.confirm_bars:
                self._try_entry(ticker, bar["close"], desired)
                st.pending_trend = 0
                st.confirm_count = 0

        self._save_state()

    def _try_entry(self, ticker: str, price: float, direction: int):
        """Runs entry filters then submits order."""
        st = self.states[ticker]

        # Post-open wait gate
        now_et = self._utc_to_et(datetime.utcnow())
        minutes_since_open = (datetime.combine(now_et.date(),
                              now_et.time()) - datetime.combine(now_et.date(),
                              dtime(9, 30))).total_seconds() / 60
        if st.wait_minutes > 0 and minutes_since_open < st.wait_minutes:
            log.info(f"BLOCK|{ticker}|WAIT_GATE|{minutes_since_open:.0f}min since open")
            return

        # ATR relative filter
        atr = st.erga._atr_smooth
        if CONFIG["atr_rel_min"] > 0 and (atr / price) < CONFIG["atr_rel_min"]:
            log.info(f"BLOCK|{ticker}|ATR_REL|{atr/price:.4f}")
            return

        # ATR historical filter
        if CONFIG["atr_hist_mult"] > 0 and st.erga._atr_sma_buf:
            hist_atr = sum(st.erga._atr_sma_buf) / len(st.erga._atr_sma_buf)
            if atr > hist_atr * CONFIG["atr_hist_mult"]:
                log.info(f"BLOCK|{ticker}|ATR_HIST|curr={atr:.2f} hist={hist_atr:.2f}")
                return

        # ADX filter
        if CONFIG["adx_min"] > 0 and st.erga._adx_smooth < CONFIG["adx_min"]:
            log.info(f"BLOCK|{ticker}|ADX|{st.erga._adx_smooth:.1f}")
            return

        # Portfolio limits
        if not self._exposure_ok(ticker, price):
            log.info(f"BLOCK|{ticker}|EXPOSURE_LIMIT")
            return
        if not self._position_count_ok(ticker):
            log.info(f"BLOCK|{ticker}|POSITION_COUNT")
            return

        # Calculate quantity
        qty = floor(st.dollar / price)
        if qty < 1:
            log.info(f"BLOCK|{ticker}|QTY_ZERO|dollar={st.dollar} price={price:.2f}")
            return

        # Close opposite position first (SAR)
        current = self._current_direction(ticker)
        if current != 0 and current != direction:
            self._submit_order(ticker, -self._current_qty(ticker), "SAR_CLOSE")

        # Open new position
        order_qty = qty if direction == 1 else -qty
        self._submit_order(ticker, order_qty, f"ERGA_{'LONG' if direction==1 else 'SHORT'}")
        st.last_signal = "LONG" if direction == 1 else "SHORT"

        log.info(f"CONF|{ticker}|{'LONG' if direction==1 else 'SHORT'}|qty={qty}|"
                 f"price={price:.2f}|ER={st.erga.er:.2f}|Q={st.erga.quality:.0f}|"
                 f"len={st.erga.active_len}")

    def _submit_order(self, ticker: str, qty: int, reason: str):
        """Submits a market order to IBKR."""
        if qty == 0:
            return
        try:
            contract = self.states[ticker]._ib_contract
            action   = "BUY" if qty > 0 else "SELL"
            order    = MarketOrder(action, abs(qty))
            trade    = self.ib.placeOrder(contract, order)
            log.info(f"FILL|{ticker}|{action}|qty={abs(qty)}|reason={reason}|orderId={trade.order.orderId}")
        except Exception as e:
            log.error(f"ERROR|ORDER|{ticker}|{e}")

    # -------------------------------------------------------------------------
    # Portfolio helpers
    # -------------------------------------------------------------------------

    def _current_direction(self, ticker: str) -> int:
        pos = self.ib.portfolio()
        for p in pos:
            if p.contract.symbol == ticker:
                return 1 if p.position > 0 else -1 if p.position < 0 else 0
        return 0

    def _current_qty(self, ticker: str) -> int:
        pos = self.ib.portfolio()
        for p in pos:
            if p.contract.symbol == ticker:
                return int(p.position)
        return 0

    def _exposure_ok(self, ticker: str, price: float) -> bool:
        total = sum(abs(p.marketValue) for p in self.ib.portfolio())
        return total < CONFIG["max_exposure_dollars"]

    def _position_count_ok(self, ticker: str) -> bool:
        invested = sum(1 for p in self.ib.portfolio() if p.position != 0)
        if self._current_direction(ticker) != 0:
            return True
        return invested < CONFIG["max_positions"]

    # -------------------------------------------------------------------------
    # Scheduled tasks
    # -------------------------------------------------------------------------

    def audit_log(self):
        """Periodic position audit — called by scheduler."""
        portfolio = {p.contract.symbol: p for p in self.ib.portfolio()}
        lines = [f"AUDIT|{datetime.utcnow().isoformat()}|symbols={len(self.states)}"]
        for ticker, st in self.states.items():
            pos  = portfolio.get(ticker)
            side = ("LONG" if pos.position > 0 else "SHORT" if pos.position < 0 else "FLAT") if pos else "FLAT"
            lines.append(
                f"  {ticker}|{side}|"
                f"ER={st.erga.er:.2f}|Q={st.erga.quality:.0f}|"
                f"len={st.erga.active_len}|vol_f={st.erga.vol_factor:.2f}"
            )
        log.info("\n".join(lines))

    # -------------------------------------------------------------------------
    # State file for dashboard
    # -------------------------------------------------------------------------

    def _save_state(self):
        """Writes current state to JSON file for the Streamlit dashboard to read."""
        portfolio = {p.contract.symbol: p for p in self.ib.portfolio()}
        data = {
            "updated": datetime.utcnow().isoformat(),
            "symbols": {}
        }
        for ticker, st in self.states.items():
            pos  = portfolio.get(ticker)
            side = ("LONG" if pos.position > 0 else "SHORT" if pos.position < 0 else "FLAT") if pos else "FLAT"
            pnl  = float(pos.unrealizedPNL) if pos else 0.0
            data["symbols"][ticker] = {
                "side":        side,
                "position_qty": int(pos.position) if pos else 0,
                "unrealized_pnl": pnl,
                "er_pct":      round(st.erga.er * 100, 1),
                "quality":     round(st.erga.quality, 1),
                "active_len":  st.erga.active_len,
                "vol_factor":  round(st.erga.vol_factor, 3),
                "trend":       st.erga.trend,
                "calc_mode":   st.erga.calc_mode,
                "timeframe":   f"{st.tf_minutes}m",
                "last_signal": st.last_signal,
            }
        try:
            with open(CONFIG["state_file"], "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"ERROR|STATE_SAVE|{e}")

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def _is_market_hours(self, utc_now: datetime) -> bool:
        et = self._utc_to_et(utc_now)
        if et.weekday() >= 5:
            return False
        return CONFIG["market_open"] <= et.time() <= CONFIG["market_close"]

    @staticmethod
    def _utc_to_et(utc_dt: datetime) -> datetime:
        """Approximate UTC → US/Eastern (accounts for DST at summer offset -4)."""
        # For production use pytz: import pytz; pytz.timezone("America/New_York")
        hour = utc_dt.hour
        # Approximate: EST = UTC-5, EDT = UTC-4 (March-Nov roughly)
        month = utc_dt.month
        offset = -4 if 3 <= month <= 11 else -5
        return utc_dt + timedelta(hours=offset)


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    engine = ERGAEngine()

    try:
        engine.connect()

        # Seed indicators with historical bars
        log.info("Seeding indicators with historical data...")
        for ticker, st in engine.states.items():
            try:
                contract = st._ib_contract
                bars = engine.ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="5 D",
                    barSizeSetting="1 min",
                    whatToShow="TRADES",
                    useRTH=True,
                )
                if bars:
                    # Resample to target TF and seed ERGA-SS
                    tf = st.tf_minutes
                    bucket = []
                    prev_bucket_idx = None
                    for bar in bars:
                        ts = bar.date
                        bucket_idx = int(ts.timestamp()) // (tf * 60)
                        if prev_bucket_idx is not None and bucket_idx != prev_bucket_idx:
                            if bucket:
                                st.erga.update(
                                    bucket[-1]["close"],
                                    max(b["high"] for b in bucket),
                                    min(b["low"]  for b in bucket),
                                )
                            bucket = []
                        bucket.append({
                            "open":  float(bar.open),
                            "high":  float(bar.high),
                            "low":   float(bar.low),
                            "close": float(bar.close),
                        })
                        prev_bucket_idx = bucket_idx
                log.info(f"SEEDED|{ticker}|bars={st.erga._bars}")
            except Exception as e:
                log.error(f"ERROR|SEED|{ticker}|{e}")

        # Scheduler for audit log
        scheduler = BackgroundScheduler()
        scheduler.add_job(engine.audit_log, "interval", minutes=30)
        scheduler.start()
        log.info("Scheduler started — audit every 30 minutes")
        log.info("Trading engine running. Press Ctrl+C to stop.")

        # Keep alive — ib_insync event loop
        engine.ib.run()

    except KeyboardInterrupt:
        log.info("Shutdown requested by user")
    finally:
        engine.disconnect()
        log.info("Engine stopped")
