"""
Setup B: EMA Trend Breakout (momentum).
Candidate filter (daily): B1 near EMA5 <=1%, B2 directional slope >=0.5%,
B3 RSI 30-70, B5 direction alignment.
Breakout trigger (5-min): B4 close beyond PDH (LONG) or PDL (SHORT) after 09:30.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytz

from services.scanner.data_client import DataFetchError, AuthError, get_daily_bars, get_intraday_bars
from services.scanner.indicators import ema, rsi
from services.scanner.models import Candidate, Direction, ScanResult, SetupID
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_NEAR_PCT = 0.01
_DIRECTIONAL_DRIFT_PCT = 0.005
_DIRECTIONAL_LOOKBACK = 10
_RSI_MIN = 30.0
_RSI_MAX = 70.0
_SKIP_UNTIL_H, _SKIP_UNTIL_M = 9, 30
_DAILY_LOOKBACK = 30
_EMA_PERIOD = 5
_RSI_PERIOD = 14
_TARGET_MULT = 1.5
_INTRADAY_INTERVAL = "5m"
_INTRADAY_LOOKBACK = 3


def find_candidates(
    symbols: list,
    api_key: str,
    near_pct: float = _NEAR_PCT,
    directional_drift_pct: float = _DIRECTIONAL_DRIFT_PCT,
    directional_lookback: int = _DIRECTIONAL_LOOKBACK,
    rsi_min: float = _RSI_MIN,
    rsi_max: float = _RSI_MAX,
    target_mult: float = _TARGET_MULT,
) -> tuple:
    """
    Identify daily candidates satisfying B1, B2, B3, B5.
    Returns (candidates, auth_error).
    """
    candidates = []
    for symbol, exchange in symbols:
        try:
            df = get_daily_bars(symbol, exchange, _DAILY_LOOKBACK, api_key)
        except AuthError:
            return candidates, True
        except DataFetchError as exc:
            logger.warning("Setup B candidate data error %s: %s", symbol, exc)
            continue

        if df.empty or len(df) < directional_lookback + _EMA_PERIOD + 5:
            continue

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema5 = ema(close, _EMA_PERIOD)
        rsi14 = rsi(close, _RSI_PERIOD)

        last_close = float(close.iloc[-1])
        last_ema5 = float(ema5.iloc[-1])
        last_rsi = float(rsi14.iloc[-1])

        if last_ema5 <= 0:
            continue

        # B1: near EMA5
        distance_pct = abs(last_close - last_ema5) / last_ema5
        if distance_pct > near_pct:
            continue

        # B2: directional slope
        if len(ema5) < directional_lookback + 1:
            continue
        ema5_then = float(ema5.iloc[-directional_lookback - 1])
        if ema5_then <= 0:
            continue
        slope_pct = (float(ema5.iloc[-1]) - ema5_then) / ema5_then
        if abs(slope_pct) < directional_drift_pct:
            continue

        # B3: RSI not extreme
        if not (rsi_min <= last_rsi <= rsi_max):
            continue

        # B5: direction alignment
        if slope_pct > 0:
            direction = Direction.LONG
        else:
            direction = Direction.SHORT

        # PDH / PDL from second-to-last daily bar
        if len(df) < 2:
            continue
        pdh = float(high.iloc[-2])
        pdl = float(low.iloc[-2])

        # Target: 1.5R measured move
        if direction == Direction.LONG:
            target = pdh + (pdh - last_ema5) * target_mult
        else:
            target = pdl - (last_ema5 - pdl) * target_mult

        cand = Candidate(
            symbol=symbol,
            exchange=exchange,
            ema5=last_ema5,
            slope_pct=round(slope_pct * 100, 4),
            rsi14=last_rsi,
            pdh=pdh,
            pdl=pdl,
            direction=direction,
            target=target,
        )
        candidates.append(cand)
        logger.debug("Setup B candidate: %s %s slope=%.2f%%", symbol, direction.value, slope_pct * 100)

    return candidates, False


def check_breakout(
    candidates: list,
    now_ist: datetime,
    api_key: str,
    skip_until_h: int = _SKIP_UNTIL_H,
    skip_until_m: int = _SKIP_UNTIL_M,
) -> tuple:
    """
    For each candidate, check if B4 breakout has fired on the latest 5-min bar.
    Returns (signals, auth_error).
    """
    # B6: skip opening volatility window
    skip_threshold = now_ist.replace(hour=skip_until_h, minute=skip_until_m, second=0, microsecond=0)
    if now_ist < skip_threshold:
        logger.debug("Setup B breakout check skipped — before %02d:%02d IST", skip_until_h, skip_until_m)
        return [], False

    signals = []
    for cand in candidates:
        try:
            df5 = get_intraday_bars(cand.symbol, cand.exchange, _INTRADAY_INTERVAL,
                                    _INTRADAY_LOOKBACK, api_key)
        except AuthError:
            return signals, True
        except DataFetchError as exc:
            logger.warning("Setup B breakout data error %s: %s", cand.symbol, exc)
            continue

        if df5.empty or len(df5) < 2:
            continue

        # Use second-to-last bar (last fully closed 5-min bar)
        last_closed = df5.iloc[-2]
        bar_close = float(last_closed["close"])

        # B4: breakout trigger
        if cand.direction == Direction.LONG and bar_close <= cand.pdh:
            continue
        if cand.direction == Direction.SHORT and bar_close >= cand.pdl:
            continue

        breakout_level = cand.pdh if cand.direction == Direction.LONG else cand.pdl
        now_utc = now_ist.astimezone(pytz.utc).replace(tzinfo=None)

        sig = ScanResult(
            symbol=cand.symbol,
            exchange=cand.exchange,
            setup_id=SetupID.B,
            direction=cand.direction,
            ltp=bar_close,
            ema5=cand.ema5,
            rsi14=cand.rsi14,
            target=cand.target,
            slope_pct=cand.slope_pct,
            distance_pct=round(abs(bar_close - cand.ema5) / cand.ema5 * 100, 2),
            pdh=cand.pdh,
            pdl=cand.pdl,
            breakout_level=breakout_level,
            signal_time=now_utc,
        )
        signals.append(sig)
        logger.info("Setup B breakout: %s %s bar_close=%.2f level=%.2f",
                    cand.symbol, cand.direction.value, bar_close, breakout_level)

    return signals, False
