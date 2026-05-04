"""Generic strategy functions for the backtest UI.

Each strategy signature:
    fn(df, sl_mult=1.5, tp_mult=2.0, **kwargs)
    -> (entries, exits, sl_stop, tp_stop, short_entries, short_exits)

sl_stop / tp_stop are fractional stops (e.g. 0.015 = 1.5%) per vectorbt convention.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from typing import Tuple


def _atr_stop(df: pd.DataFrame, period: int = 14) -> pd.Series:
    atr = ta.atr(df["high"], df["low"], df["close"], length=period)
    frac = atr / df["close"]
    return frac.fillna(method="bfill").fillna(0.02)


def ema_pullback(
    df: pd.DataFrame,
    sl_mult: float = 1.5,
    tp_mult: float = 2.5,
    fast: int = 5,
    slow: int = 20,
    rsi_period: int = 14,
    rsi_entry: int = 55,
    **_,
) -> Tuple:
    close = df["close"]
    fast_ema = ta.ema(close, length=fast)
    slow_ema = ta.ema(close, length=slow)
    rsi = ta.rsi(close, length=rsi_period)

    atr_frac = _atr_stop(df)
    sl_stop = (atr_frac * sl_mult).clip(0.005, 0.15)
    tp_stop = (atr_frac * tp_mult).clip(0.005, 0.30)

    trend_up = fast_ema > slow_ema
    pullback_long = (close <= fast_ema * 1.005) & (rsi < rsi_entry)
    entries = (trend_up & pullback_long).fillna(False)
    exits = (close >= fast_ema * 1.02).fillna(False)

    trend_down = fast_ema < slow_ema
    pullback_short = (close >= fast_ema * 0.995) & (rsi > (100 - rsi_entry))
    short_entries = (trend_down & pullback_short).fillna(False)
    short_exits = (close <= fast_ema * 0.98).fillna(False)

    return entries, exits, sl_stop, tp_stop, short_entries, short_exits


def rsi_reversal(
    df: pd.DataFrame,
    sl_mult: float = 1.5,
    tp_mult: float = 2.0,
    rsi_period: int = 14,
    oversold: int = 30,
    overbought: int = 70,
    ema_period: int = 50,
    **_,
) -> Tuple:
    close = df["close"]
    rsi = ta.rsi(close, length=rsi_period)
    trend_ema = ta.ema(close, length=ema_period)

    atr_frac = _atr_stop(df)
    sl_stop = (atr_frac * sl_mult).clip(0.005, 0.15)
    tp_stop = (atr_frac * tp_mult).clip(0.005, 0.30)

    entries = ((rsi < oversold) & (close > trend_ema)).fillna(False)
    exits = (rsi > 55).fillna(False)

    short_entries = ((rsi > overbought) & (close < trend_ema)).fillna(False)
    short_exits = (rsi < 45).fillna(False)

    return entries, exits, sl_stop, tp_stop, short_entries, short_exits


def ema_crossover(
    df: pd.DataFrame,
    sl_mult: float = 1.5,
    tp_mult: float = 2.0,
    fast: int = 9,
    slow: int = 21,
    **_,
) -> Tuple:
    close = df["close"]
    fast_ema = ta.ema(close, length=fast)
    slow_ema = ta.ema(close, length=slow)

    atr_frac = _atr_stop(df)
    sl_stop = (atr_frac * sl_mult).clip(0.005, 0.15)
    tp_stop = (atr_frac * tp_mult).clip(0.005, 0.30)

    above = (fast_ema > slow_ema).fillna(False)
    prev_above = above.shift(1).fillna(False)

    entries = above & ~prev_above
    exits = ~above & prev_above

    short_entries = ~above & prev_above
    short_exits = above & ~prev_above

    return entries, exits, sl_stop, tp_stop, short_entries, short_exits


STRATEGIES = {
    "ema_pullback": {
        "fn": ema_pullback,
        "label": "EMA Pullback",
        "description": "Price pulls back to fast EMA in a trending market (long + short)",
        "default_params": {
            "fast": 5, "slow": 20, "rsi_period": 14, "rsi_entry": 55,
            "sl_mult": 1.5, "tp_mult": 2.5,
        },
    },
    "rsi_reversal": {
        "fn": rsi_reversal,
        "label": "RSI Reversal",
        "description": "Buy oversold RSI dips above trend EMA; sell overbought spikes below",
        "default_params": {
            "rsi_period": 14, "oversold": 30, "overbought": 70,
            "ema_period": 50, "sl_mult": 1.5, "tp_mult": 2.0,
        },
    },
    "ema_crossover": {
        "fn": ema_crossover,
        "label": "EMA Crossover",
        "description": "Fast EMA crosses above/below slow EMA",
        "default_params": {"fast": 9, "slow": 21, "sl_mult": 1.5, "tp_mult": 2.0},
    },
}
