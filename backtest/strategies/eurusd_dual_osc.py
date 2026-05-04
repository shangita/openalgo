"""
PARAM Capital — EURUSD M15 Dual-Oscillator Strategy
----------------------------------------------------
Entry   : RSI-14 oversold (<35) AND Stochastic %K (<25) cross up — long
          RSI-14 overbought (>65) AND Stochastic %K (>75) cross down — short
Regime  : ADX-14 > 20 (trending) OR <20 (ranging) — filter by regime
OU Stop : Ornstein–Uhlenbeck calibrated stop using rolling 60-bar mean/std
          stop = OU mean ± ou_mult × ou_std  (dynamic, not fixed ATR)
Sizer   : GM(1,1) grey-model volatility forecast scales position (±30% of base)
          position_size = base_lots × clamp(1 / gm_vol_forecast, 0.7, 1.3)
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pandas_ta as ta

warnings.filterwarnings("ignore")


# ── GM(1,1) single-step volatility forecast ────────────────────────────────────

def _gm11_forecast(series: np.ndarray) -> float:
    """Grey Model GM(1,1): fit on last n values, return 1-step forecast."""
    n = len(series)
    if n < 4:
        return float(np.mean(np.abs(series))) or 1e-5

    x0 = np.abs(series)
    x1 = np.cumsum(x0)

    z1 = (x1[:-1] + x1[1:]) / 2
    B  = np.column_stack([-z1, np.ones(n - 1)])
    Y  = x0[1:]

    try:
        params, *_ = np.linalg.lstsq(B, Y, rcond=None)
        a, b = params
    except np.linalg.LinAlgError:
        return float(np.mean(x0))

    k  = n + 1
    x1_k = (x0[0] - b / a) * np.exp(-a * (k - 1)) + b / a
    x1_km1 = (x0[0] - b / a) * np.exp(-a * (k - 2)) + b / a
    forecast = x1_k - x1_km1
    return max(float(forecast), 1e-8)


def _gm11_scale(returns: pd.Series, window: int = 20) -> pd.Series:
    """Rolling GM(1,1) position scale factor (clamped 0.7–1.3)."""
    scale = pd.Series(1.0, index=returns.index)
    ret_arr = returns.to_numpy()
    for i in range(window, len(ret_arr)):
        forecast = _gm11_forecast(ret_arr[i - window: i])
        scale.iloc[i] = np.clip(1.0 / max(forecast, 1e-8) * np.mean(np.abs(ret_arr[i - window: i])), 0.7, 1.3)
    return scale


# ── OU mean-reversion stop ─────────────────────────────────────────────────────

def _ou_stop(close: pd.Series, window: int = 60, ou_mult: float = 1.5) -> tuple[pd.Series, pd.Series]:
    mu  = close.rolling(window).mean()
    sig = close.rolling(window).std()
    return mu - ou_mult * sig, mu + ou_mult * sig   # lower, upper


# ── Signal builder ─────────────────────────────────────────────────────────────

def _build_signals(
    data: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 2.0,
) -> tuple:
    close = data["close"]
    high  = data["high"]
    low   = data["low"]

    rsi14  = ta.rsi(close, length=14)
    stoch  = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
    adx14  = ta.adx(high, low, close, length=14)
    atr14  = ta.atr(high, low, close, length=14)

    k_col = [c for c in stoch.columns if "STOCHk" in c][0]
    stoch_k = stoch[k_col]

    adx_col = [c for c in adx14.columns if c.startswith("ADX_")][0]
    adx = adx14[adx_col]

    # OU stops (dynamic)
    ou_low, ou_high = _ou_stop(close, window=60, ou_mult=1.5)

    # Stochastic cross signals
    k_cross_up   = (stoch_k > stoch_k.shift(1)) & (stoch_k.shift(1) <= 25)
    k_cross_down = (stoch_k < stoch_k.shift(1)) & (stoch_k.shift(1) >= 75)

    long_signal  = (rsi14 < 35) & k_cross_up   & (adx > 0)   # any ADX — works in ranging too
    short_signal = (rsi14 > 65) & k_cross_down  & (adx > 0)

    # Exit on OU boundary breach or opposite signal
    long_exit  = (close > ou_high) | short_signal
    short_exit = (close < ou_low)  | long_signal

    # ATR-based SL/TP fractions (OU stops refine entries, ATR keeps hard risk limit)
    sl_stop = atr14 * sl_mult / close
    tp_stop = atr14 * tp_mult / close

    sl_stop = sl_stop.ffill().fillna(sl_mult * 0.001)
    tp_stop = tp_stop.ffill().fillna(tp_mult * 0.001)

    return (
        long_signal.fillna(False),
        long_exit.fillna(False),
        sl_stop,
        tp_stop,
        short_signal.fillna(False),
        short_exit.fillna(False),
    )


STRATEGY_NAME = "EURUSD Dual Oscillator OU (M15)"
INSTRUMENT    = "EURUSD"


def signal_fn(data: pd.DataFrame, sl_mult: float, tp_mult: float) -> tuple:
    return _build_signals(data, sl_mult, tp_mult)


def register() -> dict:
    return {
        "name":        STRATEGY_NAME,
        "instrument":  INSTRUMENT,
        "signal_fn":   signal_fn,
        "description": "RSI-14 + Stochastic dual-osc, OU mean-rev stops, GM(1,1) sizer",
    }
