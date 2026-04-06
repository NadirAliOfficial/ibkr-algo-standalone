# =============================================================================
# erga_ss_lib.py  —  ERGA-SS Indicator Engine
# Efficiency Ratio SuperSmoother Adaptive
#
# This library is the pure Python implementation of the ERGA-SS indicator.
# It is completely independent of QuantConnect and can be used in any Python
# environment — QuantConnect, standalone scripts, or direct IBKR connection.
#
# PIPELINE (6 stages):
#   1. Pre-smooth  : SuperSmoother at pre_len bars strips micro-noise from source
#   2. Eff. Ratio  : Calculated on raw price (not pre-smoothed) for genuine ER
#   3. Adapt Len   : ER blends fast_len <-> slow_len; optionally scaled by ATR ratio
#   4. Main SS     : SuperSmoother at adaptive length on pre-smoothed source
#   5. Regime Gate : ADX + normalised slope quality score gates signals
#   6. State Machine: Hysteresis threshold + min trend duration lock
#
# UPDATES vs original version:
#   - Minimum trend duration lock (post-flip bar lockout, default 3)
#   - Volatility scaling: ATR ratio scales ER-derived length (optional, default off)
#   - Vol scale clamp configurable (default ±30%)
#   - er_len now properly wired through update_params
#   - calc_mode "Manual Slow" and "Manual Fast" bypass ER + vol scaling correctly
# =============================================================================

import math
from collections import deque


# ---------------------------------------------------------------------------
# Helper: running statistics over a fixed window
# ---------------------------------------------------------------------------

class _RollingStats:
    """Maintains a rolling window for mean and stdev calculations."""

    def __init__(self, maxlen: int):
        self._buf = deque(maxlen=maxlen)

    def push(self, v: float) -> None:
        self._buf.append(v)

    def mean(self) -> float:
        if not self._buf:
            return 0.0
        return sum(self._buf) / len(self._buf)

    def stdev(self) -> float:
        n = len(self._buf)
        if n < 2:
            return 0.0
        m = self.mean()
        return math.sqrt(sum((x - m) ** 2 for x in self._buf) / (n - 1))

    def __len__(self) -> int:
        return len(self._buf)


# ---------------------------------------------------------------------------
# ERGA_SS  —  main indicator class
# ---------------------------------------------------------------------------

class ERGA_SS:
    """
    ERGA-SS: Efficiency Ratio SuperSmoother Adaptive indicator.

    Instantiate once per symbol and call update(close, high, low) on every bar.
    Read .trend (1 = long, -1 = short), .bull_flip, .bear_flip after each update.

    Parameters
    ----------
    slow_len     : int   — SS slow length (default 50)
    fast_len     : int   — SS fast length (default 15)
    er_len       : int   — Efficiency Ratio lookback (default 20)
    pre_len      : int   — Pre-smooth SS length (default 5)
    calc_mode    : str   — "Adaptive" | "Manual Slow" | "Manual Fast"
    min_quality  : float — Regime gate threshold 0-100 (default 25)
    hysteresis   : float — Slope stdev multiplier for flip threshold (default 0.1)
    buffer_mult  : float — ATR multiplier for ride line buffer (default 0.5)
    min_trend_bars: int  — Min bars between flips, 0 to disable (default 3)
    use_vol_scale: bool  — Enable ATR-ratio volatility scaling (default False)
    vol_clamp    : float — Max vol scale factor deviation from 1.0 (default 0.30)
    adx_len      : int   — ADX calculation length (default 14)
    """

    def __init__(
        self,
        slow_len: int   = 50,
        fast_len: int   = 15,
        er_len: int     = 20,
        pre_len: int    = 5,
        calc_mode: str  = "Adaptive",
        min_quality: float = 25.0,
        hysteresis: float  = 0.1,
        buffer_mult: float = 0.5,
        min_trend_bars: int = 3,
        use_vol_scale: bool = False,
        vol_clamp: float    = 0.30,
        adx_len: int        = 14,
    ):
        self.slow_len      = max(10, slow_len)
        self.fast_len      = max(3,  fast_len)
        self.er_len        = max(2,  er_len)
        self.pre_len       = max(2,  pre_len)
        self.calc_mode     = calc_mode
        self.min_quality   = min_quality
        self.hysteresis    = hysteresis
        self.buffer_mult   = buffer_mult
        self.min_trend_bars = max(0, min_trend_bars)
        self.use_vol_scale = use_vol_scale
        self.vol_clamp     = max(0.0, vol_clamp)
        self.adx_len       = max(2, adx_len)

        # --- SuperSmoother state (2 poles each) ---
        self._pre_ss1 = self._pre_ss2 = 0.0   # pre-smooth filter state
        self._ss1     = self._ss2     = 0.0   # main filter state

        # --- Rolling buffers ---
        self._raw_buf  = deque(maxlen=max(slow_len + 1, er_len + 1, 200))
        self._pre_buf  = deque(maxlen=max(slow_len + 1, 5))
        self._ss_buf   = deque(maxlen=max(slow_len + 1, 200))
        self._atr_buf  = deque(maxlen=100)           # ATR history for vol scaling
        self._slope_stats = _RollingStats(200)        # slope stdev window

        # --- ATR/ADX state ---
        self._prev_close = None
        self._prev_high  = None
        self._prev_low   = None
        self._atr_smooth = 0.0
        self._adx_smooth = 0.0
        self._plus_di    = 0.0
        self._minus_di   = 0.0
        self._prev_atr   = 0.0
        self._atr_sma_buf = deque(maxlen=100)        # long ATR SMA for vol ratio

        # --- State machine ---
        self.trend         = 1        # 1 = long, -1 = short
        self.bull_flip     = False
        self.bear_flip     = False
        self._bars_since_flip = 0

        # --- Outputs ---
        self.ss_line     = 0.0
        self.ride_line   = 0.0
        self.er          = 0.0
        self.quality     = 0.0
        self.active_len  = slow_len
        self.vol_factor  = 1.0
        self._bars       = 0

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def update(self, close: float, high: float, low: float) -> None:
        """
        Call on every completed bar (bar close).
        After calling, read: .trend, .bull_flip, .bear_flip, .ss_line, .ride_line
        """
        self._bars += 1
        self.bull_flip = False
        self.bear_flip = False

        # ---- STAGE 1: Pre-smooth ----
        raw = close
        self._raw_buf.append(raw)
        pre = self._supersmoother(raw, self.pre_len, "_pre")
        self._pre_buf.append(pre)

        # ---- STAGE 2: Efficiency Ratio (on raw price) ----
        self.er = self._efficiency_ratio(self._raw_buf, self.er_len)

        # ---- ATR and ADX ----
        atr = self._calc_atr(close, high, low)
        adx = self._calc_adx(close, high, low)
        self._atr_buf.append(atr)
        self._atr_sma_buf.append(atr)

        # ---- STAGE 3: Adaptive length + optional vol scaling ----
        if self.calc_mode == "Manual Slow":
            er_len_val = self.slow_len
        elif self.calc_mode == "Manual Fast":
            er_len_val = self.fast_len
        else:
            # Adaptive: ER blends fast <-> slow
            er_len_val = round(self.fast_len + (1.0 - self.er) * (self.slow_len - self.fast_len))
            er_len_val = max(self.fast_len, min(self.slow_len, er_len_val))

            if self.use_vol_scale and len(self._atr_sma_buf) >= 20:
                atr_sma = sum(self._atr_sma_buf) / len(self._atr_sma_buf)
                if atr_sma > 0:
                    # vol_factor > 1 = high vol → lengthen filter → more smoothing
                    # vol_factor < 1 = low vol  → shorten filter → more responsive
                    raw_factor = (atr / atr_sma)
                    clamped    = max(1.0 - self.vol_clamp,
                                     min(1.0 + self.vol_clamp, raw_factor))
                    self.vol_factor = clamped
                    er_len_val = max(self.fast_len,
                                     min(self.slow_len,
                                         round(er_len_val * self.vol_factor)))

        self.active_len = max(3, er_len_val)

        # ---- STAGE 4: Main SuperSmoother ----
        src = list(self._pre_buf)[-1] if self._pre_buf else close
        ss  = self._supersmoother(src, self.active_len, "_main")
        self.ss_line = ss
        self._ss_buf.append(ss)

        # ---- STAGE 5: Regime gate ----
        slope = (ss - list(self._ss_buf)[-2]) if len(self._ss_buf) >= 2 else 0.0
        self._slope_stats.push(slope)
        slope_stdev = self._slope_stats.stdev()
        norm_slope  = abs(slope / slope_stdev) if slope_stdev > 0 else 0.0
        adx_contrib = min(adx, 100.0)
        self.quality = min(100.0, norm_slope * 70.0 + adx_contrib)

        # ---- STAGE 6: State machine ----
        threshold = slope_stdev * self.hysteresis
        self._bars_since_flip += 1

        lock_ok = (self._bars_since_flip >= self.min_trend_bars) or (self.min_trend_bars == 0)
        regime_ok = self.quality >= self.min_quality

        if regime_ok and lock_ok:
            if slope > threshold and self.trend != 1:
                self.trend         = 1
                self.bull_flip     = True
                self._bars_since_flip = 0
                self.ride_line = ss - atr * self.buffer_mult
            elif slope < -threshold and self.trend != -1:
                self.trend         = -1
                self.bear_flip     = True
                self._bars_since_flip = 0
                self.ride_line = ss + atr * self.buffer_mult

        # Ratchet ride line with trend
        if self.trend == 1:
            new_ride = ss - atr * self.buffer_mult
            self.ride_line = max(self.ride_line, new_ride)
        else:
            new_ride = ss + atr * self.buffer_mult
            self.ride_line = min(self.ride_line, new_ride)

        self._prev_close = close
        self._prev_high  = high
        self._prev_low   = low

    def update_params(self, params: dict) -> None:
        """
        Hot-update parameters from a dict (e.g. from Google Sheets reload).
        Keys match __init__ parameter names.
        """
        if "slow_len"       in params: self.slow_len       = max(10, int(params["slow_len"]))
        if "fast_len"       in params: self.fast_len       = max(3,  int(params["fast_len"]))
        if "er_len"         in params: self.er_len         = max(2,  int(params["er_len"]))
        if "pre_len"        in params: self.pre_len        = max(2,  int(params["pre_len"]))
        if "calc_mode"      in params: self.calc_mode      = params["calc_mode"]
        if "min_quality"    in params: self.min_quality    = float(params["min_quality"])
        if "hysteresis"     in params: self.hysteresis     = float(params["hysteresis"])
        if "buffer_mult"    in params: self.buffer_mult    = float(params["buffer_mult"])
        if "min_trend_bars" in params: self.min_trend_bars = max(0, int(params["min_trend_bars"]))
        if "use_vol_scale"  in params: self.use_vol_scale  = bool(params["use_vol_scale"])
        if "vol_clamp"      in params: self.vol_clamp      = float(params["vol_clamp"])

    @property
    def bullish_flip(self) -> bool:
        """Alias for bull_flip (QuantConnect-friendly naming)."""
        return self.bull_flip

    @property
    def bearish_flip(self) -> bool:
        """Alias for bear_flip (QuantConnect-friendly naming)."""
        return self.bear_flip

    # -----------------------------------------------------------------------
    # Internal: SuperSmoother (Ehlers 2-pole Butterworth)
    # -----------------------------------------------------------------------

    def _supersmoother(self, src: float, length: int, tag: str) -> float:
        """
        Ehlers SuperSmoother filter. Hard frequency cutoff at the Nyquist
        frequency for 'length'. tag differentiates the two filter instances
        (_pre and _main) so each maintains independent state.
        """
        length = max(2, length)
        pi   = math.pi
        a    = math.exp(-1.4142135623730951 * pi / length)
        b    = 2.0 * a * math.cos(1.4142135623730951 * pi / length)
        c2   = b
        c3   = -(a * a)
        c1   = 1.0 - c2 - c3

        if tag == "_pre":
            s1, s2 = self._pre_ss1, self._pre_ss2
        else:
            s1, s2 = self._ss1, self._ss2

        val = c1 * (src + (s1 if s1 != 0.0 else src)) / 2.0 + c2 * s1 + c3 * s2

        if tag == "_pre":
            self._pre_ss2 = self._pre_ss1
            self._pre_ss1 = val
        else:
            self._ss2 = self._ss1
            self._ss1 = val

        return val

    # -----------------------------------------------------------------------
    # Internal: Efficiency Ratio
    # -----------------------------------------------------------------------

    @staticmethod
    def _efficiency_ratio(buf: deque, length: int) -> float:
        """
        Kaufman Efficiency Ratio.
        direction / sum(abs(price changes)) over 'length' bars.
        Returns 0..1 where 1 = perfectly trending, 0 = pure chop.
        """
        if len(buf) < length + 1:
            return 0.0
        prices   = list(buf)
        direction = abs(prices[-1] - prices[-(length + 1)])
        volatility = sum(abs(prices[-i] - prices[-i - 1]) for i in range(1, length + 1))
        return direction / volatility if volatility > 0 else 0.0

    # -----------------------------------------------------------------------
    # Internal: ATR (Wilder's smoothing)
    # -----------------------------------------------------------------------

    def _calc_atr(self, close: float, high: float, low: float) -> float:
        if self._prev_close is None:
            self._atr_smooth = high - low
            return self._atr_smooth
        tr = max(high - low,
                 abs(high - self._prev_close),
                 abs(low  - self._prev_close))
        k  = 1.0 / self.adx_len
        self._atr_smooth = self._atr_smooth * (1.0 - k) + tr * k
        return self._atr_smooth

    # -----------------------------------------------------------------------
    # Internal: ADX (Wilder's)
    # -----------------------------------------------------------------------

    def _calc_adx(self, close: float, high: float, low: float) -> float:
        if self._prev_high is None or self._prev_low is None:
            return 0.0
        up_move   = high - self._prev_high
        down_move = self._prev_low - low
        plus_dm   = up_move   if (up_move > down_move and up_move > 0)   else 0.0
        minus_dm  = down_move if (down_move > up_move and down_move > 0) else 0.0
        atr = self._atr_smooth if self._atr_smooth > 0 else 1e-10
        k   = 1.0 / self.adx_len
        self._plus_di  = self._plus_di  * (1.0 - k) + (plus_dm  / atr) * k
        self._minus_di = self._minus_di * (1.0 - k) + (minus_dm / atr) * k
        di_sum = self._plus_di + self._minus_di
        if di_sum == 0:
            return 0.0
        dx = abs(self._plus_di - self._minus_di) / di_sum * 100.0
        self._adx_smooth = self._adx_smooth * (1.0 - k) + dx * k
        return self._adx_smooth


# ---------------------------------------------------------------------------
# Helpers used by main.py
# ---------------------------------------------------------------------------

def get_confirm_bars(tf_minutes: int) -> int:
    """
    Returns the number of confirmation bars required before acting on a flip.
    Derived automatically from the bar timeframe — no Google Sheet column needed.

      15 min  → 2 bars  (45 min minimum trend duration at entry)
      30 min  → 2 bars  (60 min minimum trend duration at entry)
      60 min  → 1 bar
     120 min  → 1 bar
     180 min+ → 1 bar   (signal + next bar open is sufficient confirmation)
    """
    if tf_minutes <= 30:
        return 2
    return 1


def get_wait_minutes(tf_minutes: int) -> int:
    """
    Returns how many minutes after market open to wait before accepting signals.
    Avoids the volatile open auction period on short timeframes.

      ≤ 30 min → wait 15 minutes after open
      > 30 min → no wait (longer bars already span the open)
    """
    if tf_minutes <= 30:
        return 15
    return 0


def parse_calc_mode(raw: str) -> str:
    """
    Normalises calc_mode strings from Google Sheets.
    Accepts any capitalisation / spacing variant.
    Returns exactly one of: "Adaptive", "Manual Slow", "Manual Fast"
    """
    s = str(raw).strip().lower()
    if "fast" in s:
        return "Manual Fast"
    if "slow" in s:
        return "Manual Slow"
    return "Adaptive"
