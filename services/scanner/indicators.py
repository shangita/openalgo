"""
Pure NumPy/pandas technical indicators — no TA-Lib dependency.
All smoothing uses Wilder's method to match TradingView / Kite outputs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard EMA, span-based)."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI with Wilder smoothing (RMA / EWM with alpha=1/period).
    Matches TradingView Pine Script default RSI.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range with Wilder smoothing.
    TR = max(H-L, |H-Cprev|, |L-Cprev|)
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    alpha = 1.0 / period
    return tr.ewm(alpha=alpha, adjust=False).mean()


def swing_low(low: pd.Series, window: int = 5) -> float:
    """Most recent swing low in the last `window` bars."""
    return float(low.iloc[-window:].min())


def swing_high(high: pd.Series, window: int = 5) -> float:
    """Most recent swing high in the last `window` bars."""
    return float(high.iloc[-window:].max())
