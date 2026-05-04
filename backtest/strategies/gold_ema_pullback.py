"""
PARAM Capital — GOLDM 5-min EMA Trend Pullback Strategy
--------------------------------------------------------
Regime  : EMA-200 trend filter
Entry   : price pulls back to EMA-50 in an uptrend (long only)
          price pulls back to EMA-50 in a downtrend (short only)
Signal  : RSI-14 oversold (<40 long / >60 short) on pullback candle
Stop    : ATR-14 × sl_mult below/above entry
Target  : ATR-14 × tp_mult above/below entry
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

from config import INSTRUMENTS


def _build_signals(
    data: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 2.3,
) -> tuple:
    close = data["close"]
    high  = data["high"]
    low   = data["low"]

    ema50  = ta.ema(close, length=50)
    ema200 = ta.ema(close, length=200)
    rsi14  = ta.rsi(close, length=14)
    atr14  = ta.atr(high, low, close, length=14)

    uptrend   = close > ema200
    downtrend = close < ema200

    # Pullback: price touches ±2% of EMA-50
    near_ema50 = (close - ema50).abs() / ema50 < 0.002

    long_signal  = uptrend   & near_ema50 & (rsi14 < 40)
    short_signal = downtrend & near_ema50 & (rsi14 > 60)

    # Build exits: opposite direction signal triggers exit
    long_exit  = short_signal | downtrend
    short_exit = long_signal  | uptrend

    sl_stop = atr14 * sl_mult / close   # fraction of entry price
    tp_stop = atr14 * tp_mult / close

    # Fill forward so every bar has a valid multiplier
    sl_stop = sl_stop.ffill().fillna(sl_mult * 0.01)
    tp_stop = tp_stop.ffill().fillna(tp_mult * 0.01)

    return (
        long_signal.fillna(False),
        long_exit.fillna(False),
        sl_stop,
        tp_stop,
        short_signal.fillna(False),
        short_exit.fillna(False),
    )


# ── Public interface ────────────────────────────────────────────────────────────

STRATEGY_NAME = "GOLDM EMA Pullback (5min)"
INSTRUMENT    = "GOLDM"


def signal_fn(data: pd.DataFrame, sl_mult: float, tp_mult: float) -> tuple:
    return _build_signals(data, sl_mult, tp_mult)


def register() -> dict:
    return {
        "name":        STRATEGY_NAME,
        "instrument":  INSTRUMENT,
        "signal_fn":   signal_fn,
        "description": "EMA-200 trend filter + EMA-50 pullback + RSI-14; ATR stops",
    }
