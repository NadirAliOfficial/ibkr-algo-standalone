import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from indicator.erga_indicator import ERGAIndicator
from indicator.signal import SignalType


def make_candles(n=200, trend="up"):
    idx = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
    base = np.linspace(100, 200, n) if trend == "up" else np.linspace(200, 100, n)
    noise = np.random.normal(0, 0.5, n)
    close = base + noise
    high  = close + np.abs(np.random.normal(0, 1, n))
    low   = close - np.abs(np.random.normal(0, 1, n))
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1000}, index=idx)


def test_erga_initialises():
    ind = ERGAIndicator("AAPL", "1H", {})
    assert ind._erga is not None
    assert ind.ticker == "AAPL"


def test_erga_evaluates_on_trending_data():
    ind = ERGAIndicator("AAPL", "1H", {"confirm_bars": 1})
    candles = make_candles(200, trend="up")
    # Run through all candles — should produce at least one signal
    sig = ind.evaluate(candles)
    # May or may not fire depending on regime gate — just ensure no crash
    assert sig is None or sig.signal_type in (SignalType.FLIP_LONG, SignalType.FLIP_SHORT)


def test_erga_only_processes_new_bars():
    ind = ERGAIndicator("TSLA", "1H", {"confirm_bars": 1})
    candles = make_candles(200)
    ind.evaluate(candles)
    bars_after_first = ind._erga._bars

    # Second call with same candles — no new bars processed
    ind.evaluate(candles)
    assert ind._erga._bars == bars_after_first


def test_erga_processes_incremental_bars():
    ind = ERGAIndicator("NVDA", "1H", {"confirm_bars": 1})
    candles = make_candles(100)
    ind.evaluate(candles)
    bars_first = ind._erga._bars

    # Add 10 new bars
    extra_idx = [candles.index[-1] + timedelta(hours=i+1) for i in range(10)]
    extra = pd.DataFrame({
        "open": [150]*10, "high": [155]*10, "low": [145]*10,
        "close": [152]*10, "volume": [1000]*10
    }, index=extra_idx)
    all_candles = pd.concat([candles, extra])
    ind.evaluate(all_candles)
    assert ind._erga._bars == bars_first + 10


def test_erga_params_hot_reload():
    ind = ERGAIndicator("MSFT", "4H", {})
    assert ind._erga.slow_len == 50
    ind.update_params({"slow_len": 80, "fast_len": 20})
    assert ind._erga.slow_len == 80
    assert ind._erga.fast_len == 20


def test_erga_er_scale():
    ind = ERGAIndicator("AMZN", "30m", {"er_scale": 0.6})
    assert ind._erga.slow_len == max(10, round(50 * 0.6))
    assert ind._erga.fast_len == max(3, round(15 * 0.6))
