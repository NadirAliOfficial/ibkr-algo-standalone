import logging
import pandas as pd
from typing import Optional
from indicator.base import BaseIndicator
from indicator.signal import Signal, SignalType
from indicator.erga_ss_lib import ERGA_SS, get_confirm_bars, get_wait_minutes, parse_calc_mode

logger = logging.getLogger(__name__)

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
    "adx_len":        14,
}


def _build_erga_params(params: dict) -> dict:
    p = dict(ERGA_DEFAULTS)
    er_scale = float(params.get("er_scale", 0) or 0)
    if er_scale > 0:
        p["slow_len"] = max(10, round(ERGA_DEFAULTS["slow_len"] * er_scale))
        p["fast_len"] = max(3,  round(ERGA_DEFAULTS["fast_len"] * er_scale))
    for key in ERGA_DEFAULTS:
        if key in params and params[key] != "":
            raw = params[key]
            if key in ("min_quality", "hysteresis", "buffer_mult", "vol_clamp"):
                p[key] = float(raw)
            elif key == "calc_mode":
                p[key] = parse_calc_mode(str(raw))
            elif key == "use_vol_scale":
                p[key] = bool(raw)
            else:
                p[key] = int(raw)
    return p


class ERGAIndicator(BaseIndicator):
    """
    ERGA-SS indicator wrapped in BaseIndicator.

    - Processes only new bars since last evaluate() call (stateful, incremental)
    - Confirmation bars: configurable via params or auto-derived from timeframe
    - Duplicate suppression inherited from BaseIndicator
    """

    def __init__(self, ticker: str, timeframe: str, params: dict):
        super().__init__(ticker, timeframe, params)
        self._erga: Optional[ERGA_SS] = None
        self._last_bar_time = None
        self._pending_trend: Optional[int] = None
        self._confirm_count: int = 0
        self._tf_minutes: int = self._parse_tf(timeframe)
        self._confirm_bars: int = params.get("confirm_bars") or get_confirm_bars(self._tf_minutes)
        self._init_erga(params)

    def _init_erga(self, params: dict):
        p = _build_erga_params(params)
        self._erga = ERGA_SS(**p)
        logger.info(
            f"{self.ticker}: ERGA-SS init — "
            f"mode={self._erga.calc_mode} slow={self._erga.slow_len} "
            f"fast={self._erga.fast_len} confirm={self._confirm_bars}"
        )

    def update_params(self, params: dict):
        super().update_params(params)
        self._tf_minutes = self._parse_tf(self.timeframe)
        self._confirm_bars = params.get("confirm_bars") or get_confirm_bars(self._tf_minutes)
        self._erga.update_params(_build_erga_params(params))

    def evaluate(self, candles: pd.DataFrame) -> Optional[Signal]:
        """
        Feeds only new (unseen) bars to ERGA-SS, then checks for confirmed flip.
        """
        if candles is None or len(candles) < 2:
            return None

        # Find new bars since last processed
        if self._last_bar_time is not None:
            new_bars = candles[candles.index > self._last_bar_time]
        else:
            new_bars = candles

        if new_bars.empty:
            return None

        signal_type: Optional[SignalType] = None

        for bar_time, row in new_bars.iterrows():
            self._erga.update(float(row["close"]), float(row["high"]), float(row["low"]))
            self._last_bar_time = bar_time

            desired = self._erga.trend  # 1 = long, -1 = short

            # Confirmation logic (mirrors client's erga_standalone.py)
            if self._pending_trend != desired:
                self._pending_trend = desired
                self._confirm_count = 1
            else:
                self._confirm_count += 1

            if self._confirm_count >= self._confirm_bars:
                if desired == 1:
                    signal_type = SignalType.FLIP_LONG
                else:
                    signal_type = SignalType.FLIP_SHORT
                self._confirm_count = 0

        if signal_type is None:
            return None

        # Duplicate suppression from BaseIndicator
        if signal_type == self._last_signal:
            logger.debug(f"{self.ticker}: suppressed duplicate {signal_type.value}")
            return None

        self._last_signal = signal_type
        last_bar = candles.iloc[-1]

        return Signal(
            ticker=self.ticker,
            signal_type=signal_type,
            timestamp=pd.Timestamp.utcnow().to_pydatetime(),
            close_price=float(last_bar["close"]),
            bar_time=self._last_bar_time if hasattr(self._last_bar_time, 'to_pydatetime')
                     else self._last_bar_time,
            timeframe=self.timeframe,
        )

    def _compute(self, candles: pd.DataFrame) -> Optional[SignalType]:
        # Not used — ERGAIndicator overrides evaluate() directly
        return None

    @staticmethod
    def _parse_tf(tf: str) -> int:
        tf = tf.strip().upper()
        if tf == "1D":
            return 1440
        if tf.endswith("H"):
            return int(tf[:-1]) * 60
        if tf.endswith("M"):
            return int(tf[:-1])
        return 60
