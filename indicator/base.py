import logging
import pandas as pd
from abc import ABC, abstractmethod
from typing import Optional
from indicator.signal import Signal, SignalType
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseIndicator(ABC):
    """
    Base class for all per-ticker indicator instances.

    Rules enforced here:
    - Each instance is independent — no shared state between tickers
    - Signals fire on confirmed candle close only
    - Duplicate suppression: flip_long after flip_long is ignored
    - Parameters are fully configurable per instance
    """

    def __init__(self, ticker: str, timeframe: str, params: dict):
        self.ticker = ticker
        self.timeframe = timeframe
        self.params = params
        self._last_signal: Optional[SignalType] = None

    def update_params(self, params: dict):
        """Hot-reload params — takes effect on next candle cycle."""
        self.params = params
        logger.info(f"{self.ticker}: params updated — {params}")

    def evaluate(self, candles: pd.DataFrame) -> Optional[Signal]:
        """
        Evaluate indicator on a completed candle DataFrame.

        candles: OHLCV DataFrame indexed by datetime, sorted ascending.
                 Must contain only completed (closed) candles — never the live candle.

        Returns Signal or None.
        """
        if candles is None or len(candles) < 2:
            return None

        signal_type = self._compute(candles)
        if signal_type is None:
            return None

        # Suppress duplicate signals
        if signal_type == self._last_signal:
            logger.debug(f"{self.ticker}: suppressed duplicate {signal_type.value}")
            return None

        self._last_signal = signal_type
        bar = candles.iloc[-1]
        bar_time = bar.name if isinstance(bar.name, datetime) else pd.Timestamp(bar.name).to_pydatetime()

        signal = Signal(
            ticker=self.ticker,
            signal_type=signal_type,
            timestamp=datetime.utcnow(),
            close_price=float(bar["close"]),
            bar_time=bar_time,
            timeframe=self.timeframe,
        )
        logger.info(f"{self.ticker}: {signal_type.value} @ {bar['close']} bar={bar_time}")
        return signal

    @abstractmethod
    def _compute(self, candles: pd.DataFrame) -> Optional[SignalType]:
        """
        Implement indicator logic here.
        Return SignalType.FLIP_LONG, SignalType.FLIP_SHORT, or None.
        Only use candles.iloc[-1] (last closed candle) for signal decision.
        """
