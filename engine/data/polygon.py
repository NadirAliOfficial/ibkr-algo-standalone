import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

API_KEY = os.getenv("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"

TIMEFRAME_MAP = {
    "1m":  ("1", "minute"),
    "5m":  ("5", "minute"),
    "15m": ("15", "minute"),
    "30m": ("30", "minute"),
    "1H":  ("1", "hour"),
    "2H":  ("2", "hour"),
    "3H":  ("3", "hour"),
    "4H":  ("4", "hour"),
    "1D":  ("1", "day"),
}


class PolygonData:
    """Polygon.io OHLCV fetcher with in-memory candle cache and rate-limit handling."""

    def __init__(self, api_key: str = API_KEY):
        self.api_key = api_key
        self._cache: Dict[str, pd.DataFrame] = {}  # key: ticker:timeframe
        self._last_request = 0.0
        self._min_interval = 0.12  # ~8 req/s, well under free/starter limits

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, params: dict) -> Optional[dict]:
        params["apiKey"] = self.api_key
        self._throttle()
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                logger.warning("Polygon rate limit — sleeping 60s")
                time.sleep(60)
                return self._get(url, params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Polygon request failed: {e}")
            return None

    def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        lookback_bars: int = 500,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for ticker at timeframe.
        Returns DataFrame indexed by UTC datetime, sorted ascending.
        Only completed candles are returned — the live (forming) candle is excluded.
        """
        if timeframe not in TIMEFRAME_MAP:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        multiplier, span = TIMEFRAME_MAP[timeframe]
        now = datetime.utcnow()
        from_dt = (now - timedelta(days=lookback_bars * 2)).strftime("%Y-%m-%d")
        to_dt = now.strftime("%Y-%m-%d")

        url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/{multiplier}/{span}/{from_dt}/{to_dt}"
        data = self._get(url, {"adjusted": "true", "sort": "asc", "limit": lookback_bars})

        if not data or data.get("resultsCount", 0) == 0:
            logger.warning(f"No candles returned for {ticker} {timeframe}")
            return None

        df = pd.DataFrame(data["results"])
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "time"})
        df = df[["time", "open", "high", "low", "close", "volume"]].set_index("time").sort_index()

        # Drop the last (forming) candle if it's from the current period
        df = self._drop_live_candle(df, timeframe)

        cache_key = f"{ticker}:{timeframe}"
        self._cache[cache_key] = df
        return df

    def _drop_live_candle(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Remove the last candle if it belongs to the currently forming bar."""
        if df.empty:
            return df
        now = pd.Timestamp.utcnow()
        last_bar = df.index[-1]
        duration = self._bar_duration(timeframe)
        if now < last_bar + duration:
            return df.iloc[:-1]
        return df

    def _bar_duration(self, timeframe: str) -> pd.Timedelta:
        durations = {
            "1m": pd.Timedelta(minutes=1),
            "5m": pd.Timedelta(minutes=5),
            "15m": pd.Timedelta(minutes=15),
            "30m": pd.Timedelta(minutes=30),
            "1H": pd.Timedelta(hours=1),
            "2H": pd.Timedelta(hours=2),
            "4H": pd.Timedelta(hours=4),
            "1D": pd.Timedelta(days=1),
        }
        return durations.get(timeframe, pd.Timedelta(hours=1))

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get current ask price for position sizing."""
        url = f"{BASE_URL}/v2/last/trade/{ticker}"
        data = self._get(url, {})
        if data and "results" in data:
            return float(data["results"].get("p", 0))
        return None

    def cached(self, ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
        return self._cache.get(f"{ticker}:{timeframe}")
