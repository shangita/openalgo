"""
PARAM Capital — SILVERMICM 1-min RSI Bear-Regime Strategy
----------------------------------------------------------
Regime  : Bear = price below EMA-200 on 1-min chart
Entry   : RSI-7 reaches overbought (>70) then hooks down — short entry
          RSI-7 reaches oversold  (<30) then hooks up   — long  entry (counter-trend)
Signal  : RSI cross confirmed by MACD histogram sign
Stop    : ATR-7 × sl_mult
Target  : ATR-7 × tp_mult
Volume  : only trade if volume > 20-period SMA (liquidity filter)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta


def _build_signals(
    data: pd.DataFrame,
    sl_mult: float = 1.0,
    tp_mult: float = 2.0,
) -> tuple:
    close  = data["close"]
    high   = data["high"]
    low    = data["low"]
    volume = data["volume"] if "volume" in data.columns else pd.Series(1, index=data.index)

    ema200 = ta.ema(close, length=200)
    rsi7   = ta.rsi(close, length=7)
    atr7   = ta.atr(high, low, close, length=7)
    macd   = ta.macd(close, fast=8, slow=21, signal=5)
    vol_ma = volume.rolling(20).mean()

    bear_regime = close < ema200
    liquid      = volume > vol_ma

    # RSI hook: previous bar crossed threshold, current bar reverses
    rsi_was_ob = rsi7.shift(1) > 70
    rsi_now_dn = rsi7 < rsi7.shift(1)
    rsi_was_os = rsi7.shift(1) < 30
    rsi_now_up = rsi7 > rsi7.shift(1)

    hist_col = [c for c in macd.columns if "h" in c.lower()]
    macd_hist = macd[hist_col[0]] if hist_col else pd.Series(0, index=data.index)

    short_signal = bear_regime & liquid & rsi_was_ob & rsi_now_dn & (macd_hist < 0)
    long_signal  = liquid & rsi_was_os & rsi_now_up & (macd_hist > 0)

    short_exit = long_signal  | (rsi7 < 30)
    long_exit  = short_signal | (rsi7 > 70)

    sl_stop = atr7 * sl_mult / close
    tp_stop = atr7 * tp_mult / close

    sl_stop = sl_stop.ffill().fillna(sl_mult * 0.005)
    tp_stop = tp_stop.ffill().fillna(tp_mult * 0.005)

    return (
        long_signal.fillna(False),
        long_exit.fillna(False),
        sl_stop,
        tp_stop,
        short_signal.fillna(False),
        short_exit.fillna(False),
    )


STRATEGY_NAME = "SILVERMICM RSI Bear (1min)"
INSTRUMENT    = "SILVERMICM"


def signal_fn(data: pd.DataFrame, sl_mult: float, tp_mult: float) -> tuple:
    return _build_signals(data, sl_mult, tp_mult)


def register() -> dict:
    return {
        "name":        STRATEGY_NAME,
        "instrument":  INSTRUMENT,
        "signal_fn":   signal_fn,
        "description": "Bear-regime RSI-7 mean-reversion + MACD histogram confirmation",
    }
