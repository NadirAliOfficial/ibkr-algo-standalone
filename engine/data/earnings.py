import os
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")
FMP_KEY = os.getenv("FMP_API_KEY", "")


class EarningsChecker:
    """
    Check upcoming earnings for a ticker within a look-ahead window.
    Primary: Polygon.io — fallback: Financial Modeling Prep.
    """

    def __init__(self, window_days: int = 3):
        self.window_days = window_days  # block trades if earnings within N days

    def has_earnings_soon(self, ticker: str) -> tuple[bool, Optional[str]]:
        """
        Returns (True, earnings_date_str) if earnings within window, else (False, None).
        """
        result = self._check_polygon(ticker)
        if result[0] is not None:
            return result

        logger.info(f"{ticker}: Polygon earnings check failed — trying FMP")
        return self._check_fmp(ticker)

    def _check_polygon(self, ticker: str) -> tuple[Optional[bool], Optional[str]]:
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=self.window_days)
        url = "https://api.polygon.io/vX/reference/financials"
        params = {
            "ticker": ticker,
            "timeframe": "quarterly",
            "apiKey": POLYGON_KEY,
        }
        try:
            r = requests.get(url, params=params, timeout=8)
            if r.status_code != 200:
                return None, None
            data = r.json()
            for item in data.get("results", []):
                date_str = item.get("filing_date") or item.get("period_of_report_date", "")
                if not date_str:
                    continue
                d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                if today <= d <= cutoff:
                    return True, date_str[:10]
            return False, None
        except Exception as e:
            logger.warning(f"{ticker}: Polygon earnings error: {e}")
            return None, None

    def _check_fmp(self, ticker: str) -> tuple[bool, Optional[str]]:
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=self.window_days)
        from_str = today.strftime("%Y-%m-%d")
        to_str = cutoff.strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/earning_calendar"
        params = {
            "from": from_str,
            "to": to_str,
            "apikey": FMP_KEY,
        }
        try:
            r = requests.get(url, params=params, timeout=8)
            r.raise_for_status()
            for item in r.json():
                if item.get("symbol", "").upper() == ticker.upper():
                    return True, item.get("date", "")
            return False, None
        except Exception as e:
            logger.warning(f"{ticker}: FMP earnings error: {e}")
            return False, None
