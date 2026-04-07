# =============================================================================
# erga_ss_lib.py  —  ERGA-SS Indicator Engine
# Efficiency Ratio SuperSmoother Adaptive
#
# PIPELINE (6 stages):
#   1. Pre-smooth  : SuperSmoother at pre_len bars strips micro-noise from source
#   2. Eff. Ratio  : Calculated on raw price (not pre-smoothed) for genuine ER
#   3. Adapt Len   : ER blends fast_len <-> slow_len; optionally scaled by ATR ratio
#   4. Main SS     : SuperSmoother at adaptive length on pre-smoothed source
#   5. Regime Gate : ADX + normalised slope quality score gates signals
#   6. State Machine: Hysteresis threshold + min trend duration lock
#
# VALIDATION FIXES vs original (Phase 1):
#   - Price source: close → hlc3 = (high+low+close)/3, matches Pine Script default
#   - Slope normalisation: stdev of ssLine (50 bars) matches Pine's ta.stdev(ssLine,50)
#   - Threshold: stdev window 200→100 bars, matches Pine's ta.stdev(ssLine-ssLine[1],100)
#   - State machine reordered to match Pine Script exactly (desired computed first)
# =============================================================================

import math
from collections import deque


class _RollingStats:
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


class ERGA_SS:
    """
    ERGA-SS: Efficiency Ratio SuperSmoother Adaptive indicator.

    Instantiate once per symbol and call update(close, high, low) on every bar.
    Read .trend (1=long, -1=short), .bull_flip, .bear_flip after each update.
    """

    def __init__(
        self,
        slow_len: int       = 50,
        fast_len: int       = 15,
        er_len: int         = 20,
        pre_len: int        = 5,
        calc_mode: str      = "Adaptive",
        min_quality: float  = 25.0,
        hysteresis: float   = 0.1,
        buffer_mult: float  = 0.5,
        min_trend_bars: int = 3,
        use_vol_scale: bool = False,
        vol_clamp: float    = 0.30,
        adx_len: int        = 14,
        use_hlc3: bool      = True,  # True = matches Pine Script default source (hlc3)
    ):
        self.slow_len       = max(10, slow_len)
        self.fast_len       = max(3,  fast_len)
        self.er_len         = max(2,  er_len)
        self.pre_len        = max(2,  pre_len)
        self.calc_mode      = calc_mode
        self.min_quality    = min_quality
        self.hysteresis     = hysteresis
        self.buffer_mult    = buffer_mult
        self.min_trend_bars = max(0, min_trend_bars)
        self.use_vol_scale  = use_vol_scale
        self.vol_clamp      = max(0.0, vol_clamp)
        self.adx_len        = max(2, adx_len)
        self.use_hlc3       = use_hlc3

        self._pre_ss1 = self._pre_ss2 = 0.0
        self._ss1     = self._ss2     = 0.0
        self._pre_src_prev  = 0.0
        self._main_src_prev = 0.0

        self._raw_buf     = deque(maxlen=max(slow_len + 1, er_len + 1, 200))
        self._pre_buf     = deque(maxlen=max(slow_len + 1, 5))
        self._ss_buf      = deque(maxlen=max(slow_len + 1, 200))
        self._atr_sma_buf = deque(maxlen=100)  # for vol_scale only
        self._ssline_buf  = deque(maxlen=50)   # for stdev(ssLine, 50) — matches Pine
        self._slope_stats = _RollingStats(100)  # for stdev(slope, 100) — matches Pine

        self._prev_close = None
        self._prev_high  = None
        self._prev_low   = None
        self._atr_smooth  = 0.0
        self._tr_rma      = 0.0   # separate TR RMA for ADX (matches ta.dmi)
        self._pdm_rma     = 0.0
        self._ndm_rma     = 0.0
        self._adx_smooth  = 0.0

        self.trend            = 1
        self.bull_flip        = False
        self.bear_flip        = False
        self._bars_since_flip = 0

        self.ss_line    = 0.0
        self.ride_line  = 0.0
        self.er         = 0.0
        self.quality    = 0.0
        self.active_len = slow_len
        self.vol_factor = 1.0
        self._bars      = 0

    def update(self, close: float, high: float, low: float) -> None:
        """Call on every completed bar. Uses hlc3 as source (matches Pine Script)."""
        self._bars += 1
        self.bull_flip = False
        self.bear_flip = False

        # Price source: hlc3 = (high + low + close) / 3  — Pine Script default
        raw = (high + low + close) / 3.0 if self.use_hlc3 else close

        # Stage 1: Pre-smooth
        self._raw_buf.append(raw)
        pre = self._supersmoother(raw, self.pre_len, "_pre")
        self._pre_buf.append(pre)

        # Stage 2: Efficiency Ratio on raw price
        self.er = self._efficiency_ratio(self._raw_buf, self.er_len)

        # ATR + ADX
        atr = self._calc_atr(close, high, low)
        adx = self._calc_adx(close, high, low)

        # Stage 3: Adaptive length
        if self.calc_mode == "Manual Slow":
            er_len_val = self.slow_len
        elif self.calc_mode == "Manual Fast":
            er_len_val = self.fast_len
        else:
            er_len_val = round(self.fast_len + (1.0 - self.er) * (self.slow_len - self.fast_len))
            er_len_val = max(self.fast_len, min(self.slow_len, er_len_val))
            self._atr_sma_buf.append(atr)
            if self.use_vol_scale and len(self._atr_sma_buf) >= 20:
                atr_sma = sum(self._atr_sma_buf) / len(self._atr_sma_buf)
                if atr_sma > 0:
                    raw_factor      = atr / atr_sma
                    clamped         = max(1.0 - self.vol_clamp, min(1.0 + self.vol_clamp, raw_factor))
                    self.vol_factor = clamped
                    er_len_val      = max(self.fast_len, min(self.slow_len, round(er_len_val * self.vol_factor)))

        self.active_len = max(3, er_len_val)

        # Stage 4: Main SuperSmoother
        src = list(self._pre_buf)[-1] if self._pre_buf else raw
        ss  = self._supersmoother(src, self.active_len, "_main")
        self.ss_line = ss
        prev_ss = list(self._ss_buf)[-1] if self._ss_buf else ss
        self._ss_buf.append(ss)
        self._ssline_buf.append(ss)

        # Stage 5: Regime gate
        # Normalise slope by stdev(ssLine, 50) — matches Pine Script exactly
        slope        = ss - prev_ss
        ssline_stdev = self._stdev(self._ssline_buf)
        norm_slope   = abs(slope / ssline_stdev) if ssline_stdev > 0 else 0.0
        self.quality = min(100.0, norm_slope * 70.0 + min(adx, 100.0))
        regime_ok    = self.quality >= self.min_quality

        # Stage 6: State machine
        # Threshold = stdev(slope, 100) * hysteresis — matches Pine ta.stdev(ssLine-ssLine[1], 100)
        self._slope_stats.push(slope)
        threshold = self._slope_stats.stdev() * self.hysteresis
        self._bars_since_flip += 1
        lock_ok = (self._bars_since_flip >= self.min_trend_bars) or (self.min_trend_bars == 0)

        # Compute desired direction (mirrors Pine Script ordering)
        desired = self.trend
        if slope > threshold and regime_ok:
            desired = 1
        elif slope < -threshold and regime_ok:
            desired = -1

        if desired != self.trend and lock_ok:
            self.trend            = desired
            self._bars_since_flip = 0
            if desired == 1:
                self.bull_flip = True
                self.ride_line = ss - atr * self.buffer_mult
            else:
                self.bear_flip = True
                self.ride_line = ss + atr * self.buffer_mult

        # Ratchet ride line
        if self.trend == 1:
            self.ride_line = max(self.ride_line, ss - atr * self.buffer_mult)
        else:
            self.ride_line = min(self.ride_line, ss + atr * self.buffer_mult)

        self._prev_close = close
        self._prev_high  = high
        self._prev_low   = low

    def update_params(self, params: dict) -> None:
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
        if "use_hlc3"       in params: self.use_hlc3       = bool(params["use_hlc3"])

    @property
    def bullish_flip(self) -> bool:
        return self.bull_flip

    @property
    def bearish_flip(self) -> bool:
        return self.bear_flip

    def _supersmoother(self, src: float, length: int, tag: str) -> float:
        length = max(2, length)
        a  = math.exp(-1.4142135623730951 * math.pi / length)
        b  = 2.0 * a * math.cos(1.4142135623730951 * math.pi / length)
        c2 = b
        c3 = -(a * a)
        c1 = 1.0 - c2 - c3
        if tag == "_pre":
            s1, s2   = self._pre_ss1, self._pre_ss2
            src_prev = self._pre_src_prev
        else:
            s1, s2   = self._ss1, self._ss2
            src_prev = self._main_src_prev
        # Ehlers formula: C1/2 * (src + src[1]) + C2 * SS[1] + C3 * SS[2]
        val = c1 / 2.0 * (src + src_prev) + c2 * s1 + c3 * s2
        if tag == "_pre":
            self._pre_ss2     = self._pre_ss1
            self._pre_ss1     = val
            self._pre_src_prev = src
        else:
            self._ss2           = self._ss1
            self._ss1           = val
            self._main_src_prev = src
        return val

    @staticmethod
    def _efficiency_ratio(buf: deque, length: int) -> float:
        if len(buf) < length + 1:
            return 0.0
        prices     = list(buf)
        direction  = abs(prices[-1] - prices[-(length + 1)])
        volatility = sum(abs(prices[-i] - prices[-i - 1]) for i in range(1, length + 1))
        return direction / volatility if volatility > 0 else 0.0

    def _calc_atr(self, close: float, high: float, low: float) -> float:
        if self._prev_close is None:
            self._atr_smooth = high - low
            return self._atr_smooth
        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        k  = 1.0 / self.adx_len
        self._atr_smooth = self._atr_smooth * (1.0 - k) + tr * k
        return self._atr_smooth

    def _calc_adx(self, close: float, high: float, low: float) -> float:
        """
        Matches Pine Script ta.dmi(14, 14):
        Smooth +DM, -DM, TR separately (not the ratio), then compute DI and DX.
        """
        if self._prev_high is None or self._prev_low is None:
            return 0.0
        tr       = max(self._prev_high - self._prev_low,
                       abs(self._prev_high - close),
                       abs(self._prev_low  - close))
        up_move  = high - self._prev_high
        dn_move  = self._prev_low - low
        plus_dm  = up_move if (up_move > dn_move and up_move > 0) else 0.0
        minus_dm = dn_move if (dn_move > up_move and dn_move > 0) else 0.0
        k = 1.0 / self.adx_len
        self._tr_rma  = self._tr_rma  * (1.0 - k) + tr       * k
        self._pdm_rma = self._pdm_rma * (1.0 - k) + plus_dm  * k
        self._ndm_rma = self._ndm_rma * (1.0 - k) + minus_dm * k
        tr_nz    = self._tr_rma if self._tr_rma > 0 else 1e-10
        plus_di  = self._pdm_rma / tr_nz * 100.0
        minus_di = self._ndm_rma / tr_nz * 100.0
        di_sum   = plus_di + minus_di
        dx       = abs(plus_di - minus_di) / di_sum * 100.0 if di_sum > 0 else 0.0
        self._adx_smooth = self._adx_smooth * (1.0 - k) + dx * k
        return self._adx_smooth

    @staticmethod
    def _stdev(buf: deque) -> float:
        n = len(buf)
        if n < 2:
            return 0.0
        m = sum(buf) / n
        return math.sqrt(sum((x - m) ** 2 for x in buf) / (n - 1))


def get_confirm_bars(tf_minutes: int) -> int:
    if tf_minutes <= 30:
        return 2
    return 1


def get_wait_minutes(tf_minutes: int) -> int:
    if tf_minutes <= 30:
        return 15
    return 0


def parse_calc_mode(raw: str) -> str:
    s = str(raw).strip().lower()
    if "fast" in s:
        return "Manual Fast"
    if "slow" in s:
        return "Manual Slow"
    return "Adaptive"
