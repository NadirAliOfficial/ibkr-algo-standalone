import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from indicator.base import BaseIndicator
from indicator.signal import Signal, SignalType


class DummyIndicator(BaseIndicator):
    """Returns flip_long on first call, flip_short on second — for testing base logic."""
    def __init__(self, ticker, timeframe="1H"):
        super().__init__(ticker, timeframe, {})
        self._call_count = 0

    def _compute(self, candles):
        self._call_count += 1
        return SignalType.FLIP_LONG if self._call_count % 2 == 1 else SignalType.FLIP_SHORT


def make_candles(n=50):
    idx = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
    data = {
        "open": np.random.uniform(100, 200, n),
        "high": np.random.uniform(200, 250, n),
        "low": np.random.uniform(80, 100, n),
        "close": np.random.uniform(100, 200, n),
        "volume": np.random.randint(1000, 100000, n),
    }
    return pd.DataFrame(data, index=idx)


def test_signal_fires():
    ind = DummyIndicator("AAPL")
    candles = make_candles()
    sig = ind.evaluate(candles)
    assert sig is not None
    assert sig.signal_type == SignalType.FLIP_LONG
    assert sig.ticker == "AAPL"


def test_duplicate_suppression():
    ind = DummyIndicator("AAPL")
    candles = make_candles()

    # Force _last_signal to flip_long
    ind._last_signal = SignalType.FLIP_LONG
    ind._call_count = 0  # next _compute returns flip_long

    sig = ind.evaluate(candles)
    assert sig is None  # duplicate suppressed


def test_flip_alternates():
    ind = DummyIndicator("TSLA")
    candles = make_candles()

    s1 = ind.evaluate(candles)
    s2 = ind.evaluate(candles)
    s3 = ind.evaluate(candles)

    assert s1.signal_type == SignalType.FLIP_LONG
    assert s2.signal_type == SignalType.FLIP_SHORT
    assert s3.signal_type == SignalType.FLIP_LONG


def test_params_update():
    ind = DummyIndicator("NVDA")
    ind.update_params({"length": 20, "fast": 5})
    assert ind.params == {"length": 20, "fast": 5}


def test_insufficient_candles_returns_none():
    ind = DummyIndicator("MSFT")
    sig = ind.evaluate(pd.DataFrame())
    assert sig is None
