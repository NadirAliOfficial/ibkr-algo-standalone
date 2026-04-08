"""
Polygon.io OHLCV data provider.

Bar construction rules (matches TradingView exactly):
  - 1H, 1D : fetched natively from Polygon
  - 2H/3H/4H: fetched as 1H then resampled using ET session-aligned boundaries
                starting from 09:30 ET each trading day (same as TradingView)
  - UTC timestamps throughout; all bar boundaries anchored to ET session open

Warmup: always fetches enough history for indicator convergence (≥500 bars).
Resilience: retry on 5xx/timeout, 429 backoff, gap detection with warning.
"""

import os
import time
import logging
import math
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
ET       = ZoneInfo("America/New_York")
UTC      = timezone.utc

NATIVE_TIMEFRAMES = {
    "1m":  ("1",  "minute"),
    "5m":  ("5",  "minute"),
    "15m": ("15", "minute"),
    "30m": ("30", "minute"),
    "1H":  ("1",  "hour"),
    "1D":  ("1",  "day"),
}

RESAMPLE_TIMEFRAMES = {
    "2H": ("1H", "2h"),
    "3H": ("1H", "3h"),
    "4H": ("1H", "4h"),
}

WARMUP_BARS = 500   # minimum bars fed to indicator for convergence


class PolygonData:

    def __init__(self, api_key: str = API_KEY):
        self.api_key       = api_key
        self._cache: Dict[str, pd.DataFrame] = {}
        self._last_request = 0.0
        self._min_interval = 0.13   # ~7 req/s, safe under free/starter limits

    # ── public ───────────────────────────────────────────────────────────────

    def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        warmup_bars: int = WARMUP_BARS,
    ) -> Optional[pd.DataFrame]:
        """
        Return completed OHLCV candles indexed by UTC datetime, ascending.
        Live (forming) candle is always excluded.
        Returns None on failure.
        """
        if timeframe in NATIVE_TIMEFRAMES:
            df = self._fetch_native(ticker, timeframe, warmup_bars)
        elif timeframe in RESAMPLE_TIMEFRAMES:
            src_tf, rule = RESAMPLE_TIMEFRAMES[timeframe]
            df = self._fetch_native(ticker, src_tf, warmup_bars * int(rule[0]))
            if df is not None:
                df = self._resample_et(df, rule)
        else:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        if df is None or df.empty:
            return None

        df = self._drop_live_candle(df, timeframe)
        self._cache[f"{ticker}:{timeframe}"] = df
        return df

    def get_latest_price(self, ticker: str) -> Optional[float]:
        url  = f"{BASE_URL}/v2/last/trade/{ticker}"
        data = self._get(url, {})
        if data and "results" in data:
            return float(data["results"].get("p", 0)) or None
        return None

    def cached(self, ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
        return self._cache.get(f"{ticker}:{timeframe}")

    # ── native fetch ─────────────────────────────────────────────────────────

    def _fetch_native(
        self,
        ticker: str,
        timeframe: str,
        bars_needed: int,
    ) -> Optional[pd.DataFrame]:
        mult, span = NATIVE_TIMEFRAMES[timeframe]
        days_back  = self._days_lookback(timeframe, bars_needed)
        now        = datetime.now(UTC)
        from_dt    = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_dt      = now.strftime("%Y-%m-%d")

        url    = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{from_dt}/{to_dt}"
        params = {
            "adjusted": "true",
            "sort":     "asc",
            "limit":    50000,   # max per request — pagination handled below
        }

        frames = []
        while url:
            data = self._get(url, params)
            if not data:
                break
            results = data.get("results", [])
            if results:
                frames.append(pd.DataFrame(results))
            # Polygon pagination
            url    = data.get("next_url")
            params = {}   # next_url already has all params encoded

        if not frames:
            logger.warning(f"No candles: {ticker} {timeframe}")
            return None

        df = pd.concat(frames, ignore_index=True)
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={
            "o": "open", "h": "high", "l": "low",
            "c": "close", "v": "volume", "t": "time",
        })
        df = (
            df[["time", "open", "high", "low", "close", "volume"]]
            .set_index("time")
            .sort_index()
            .dropna(subset=["close"])
        )
        self._warn_gaps(df, timeframe, ticker)
        return df

    # ── ET session-aligned resampling ─────────────────────────────────────────

    def _resample_et(self, df: pd.DataFrame, rule: str) -> pd.DataFrame:
        """
        Resample 1H bars into N-hour bars aligned to ET session open (09:30).

        TradingView builds multi-hour bars by grouping 1H bars from the session
        open (09:30 ET). This must match exactly or indicator bar boundaries differ.

        Strategy:
          1. Convert index to ET
          2. For each trading day, group 1H bars into chunks of N hours from 09:30
          3. OHLCV aggregate: O=first, H=max, L=min, C=last, V=sum
          4. Convert result index back to UTC
        """
        n_hours = int(rule[0])

        # Work in ET
        df_et = df.copy()
        df_et.index = df_et.index.tz_convert(ET)

        groups = []
        dates  = sorted(set(df_et.index.date))

        for day in dates:
            day_bars = df_et[df_et.index.date == day].copy()
            if day_bars.empty:
                continue

            # Session open anchor: 09:30 ET on this day
            session_open = pd.Timestamp(day, tz=ET).replace(hour=9, minute=30)

            # Assign each bar to a bucket: floor((bar_start - session_open) / n_hours)
            delta_hours = (day_bars.index - session_open).total_seconds() / 3600
            day_bars["_bucket"] = (delta_hours // n_hours).astype(int)

            # Only keep bars within market hours (bucket >= 0)
            day_bars = day_bars[day_bars["_bucket"] >= 0]
            if day_bars.empty:
                continue

            # Aggregate each bucket
            for bucket, chunk in day_bars.groupby("_bucket"):
                bar_time = session_open + pd.Timedelta(hours=int(bucket) * n_hours)
                groups.append({
                    "time":   bar_time.tz_convert(UTC),
                    "open":   chunk["open"].iloc[0],
                    "high":   chunk["high"].max(),
                    "low":    chunk["low"].min(),
                    "close":  chunk["close"].iloc[-1],
                    "volume": chunk["volume"].sum(),
                })

        if not groups:
            return pd.DataFrame()

        result = pd.DataFrame(groups).set_index("time").sort_index()
        return result

    # ── live candle exclusion ─────────────────────────────────────────────────

    def _drop_live_candle(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if df.empty:
            return df
        now      = pd.Timestamp.now(tz=UTC)
        last_bar = df.index[-1]
        duration = self._bar_duration(timeframe)
        if now < last_bar + duration:
            return df.iloc[:-1]
        return df

    def _bar_duration(self, timeframe: str) -> pd.Timedelta:
        durations = {
            "1m":  pd.Timedelta(minutes=1),
            "5m":  pd.Timedelta(minutes=5),
            "15m": pd.Timedelta(minutes=15),
            "30m": pd.Timedelta(minutes=30),
            "1H":  pd.Timedelta(hours=1),
            "2H":  pd.Timedelta(hours=2),
            "3H":  pd.Timedelta(hours=3),
            "4H":  pd.Timedelta(hours=4),
            "1D":  pd.Timedelta(days=1),
        }
        return durations.get(timeframe, pd.Timedelta(hours=1))

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict, retries: int = 3) -> Optional[dict]:
        if not url.startswith("http"):
            url = BASE_URL + url
        p = {**params, "apiKey": self.api_key}
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                r = requests.get(url, params=p, timeout=12)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 60))
                    logger.warning(f"Polygon 429 — sleeping {wait}s")
                    time.sleep(wait)
                    continue
                if r.status_code >= 500:
                    logger.warning(f"Polygon {r.status_code} attempt {attempt}/{retries}")
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.Timeout:
                logger.warning(f"Polygon timeout attempt {attempt}/{retries}")
                time.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Polygon request failed: {e}")
                return None
        return None

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _days_lookback(timeframe: str, bars_needed: int) -> int:
        """Estimate calendar days needed to get bars_needed trading bars."""
        minutes_per_bar = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1H": 60, "2H": 120, "3H": 180, "4H": 240, "1D": 390,
        }
        mins = minutes_per_bar.get(timeframe, 60)
        trading_mins_per_day = 390  # 6.5h market session
        trading_bars_per_day = trading_mins_per_day / mins
        trading_days_needed  = math.ceil(bars_needed / trading_bars_per_day)
        # multiply by ~1.5 to account for weekends/holidays
        return max(30, math.ceil(trading_days_needed * 1.5))

    @staticmethod
    def _warn_gaps(df: pd.DataFrame, timeframe: str, ticker: str):
        if len(df) < 2:
            return
        durations = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1H": 60, "1D": 1440,
        }
        mins = durations.get(timeframe)
        if not mins:
            return
        expected = pd.Timedelta(minutes=mins)
        diffs    = df.index.to_series().diff().dropna()
        gaps     = diffs[diffs > expected * 2]
        if len(gaps) > 0:
            logger.debug(f"{ticker} {timeframe}: {len(gaps)} bar gap(s) detected")
