"""
Setup A: EMA Sideways Pullback (mean reversion).
Conditions: A1 price away from EMA5 >=3%, A2 last 6 bars available,
A3 EMA5 sideways (<=0.5% drift over 10 bars), A4 RSI in 40-60.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytz

from services.scanner.data_client import DataFetchError, AuthError, get_daily_bars
from services.scanner.indicators import ema, rsi
from services.scanner.models import Direction, ScanResult, SetupID
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ─── Config defaults (overridden by scheduler config) ─────────────────────────
_AWAY_PCT = 0.03
_SIDEWAYS_DRIFT_PCT = 0.005
_SIDEWAYS_LOOKBACK = 10
_RSI_MIN = 40.0
_RSI_MAX = 60.0
_DAILY_LOOKBACK = 30
_EMA_PERIOD = 5
_RSI_PERIOD = 14


def scan_one(
    symbol: str,
    exchange: str,
    api_key: str,
    away_pct: float = _AWAY_PCT,
    sideways_drift_pct: float = _SIDEWAYS_DRIFT_PCT,
    sideways_lookback: int = _SIDEWAYS_LOOKBACK,
    rsi_min: float = _RSI_MIN,
    rsi_max: float = _RSI_MAX,
) -> Optional[ScanResult]:
    """
    Run Setup A for a single symbol. Returns ScanResult or None.
    Raises AuthError on Kite auth failure (propagated to scheduler).
    """
    try:
        df = get_daily_bars(symbol, exchange, _DAILY_LOOKBACK, api_key)
    except AuthError:
        raise
    except DataFetchError as exc:
        logger.warning("Setup A data error %s: %s", symbol, exc)
        return None

    if df.empty or len(df) < _SIDEWAYS_LOOKBACK + _EMA_PERIOD + 5:
        logger.debug("Setup A insufficient bars for %s (%d)", symbol, len(df))
        return None

    close = df["close"]
    ema5 = ema(close, _EMA_PERIOD)
    rsi14 = rsi(close, _RSI_PERIOD)

    last_close = float(close.iloc[-1])
    last_ema5 = float(ema5.iloc[-1])
    last_rsi = float(rsi14.iloc[-1])

    if last_ema5 <= 0:
        return None

    # A1: price away from EMA5
    distance_pct = abs(last_close - last_ema5) / last_ema5
    if distance_pct < away_pct:
        return None

    # A2: at least 6 bars available (already checked above)

    # A3: EMA5 sideways — drift over last 10 bars
    if len(ema5) < sideways_lookback + 1:
        return None
    ema5_now = float(ema5.iloc[-1])
    ema5_then = float(ema5.iloc[-sideways_lookback - 1])
    if ema5_then <= 0:
        return None
    slope_pct = (ema5_now - ema5_then) / ema5_then
    if abs(slope_pct) > sideways_drift_pct:
        return None

    # A4: RSI in neutral zone
    if not (rsi_min <= last_rsi <= rsi_max):
        return None

    # Direction
    direction = Direction.SHORT if last_close > last_ema5 else Direction.LONG

    now_ist = datetime.now(IST)
    sig = ScanResult(
        symbol=symbol,
        exchange=exchange,
        setup_id=SetupID.A,
        direction=direction,
        ltp=last_close,
        ema5=last_ema5,
        rsi14=last_rsi,
        target=last_ema5,
        slope_pct=round(slope_pct * 100, 4),
        distance_pct=round(distance_pct * 100, 2),
        pdh=None,
        pdl=None,
        breakout_level=None,
        signal_time=now_ist.astimezone(pytz.utc).replace(tzinfo=None),
    )
    logger.info("Setup A signal: %s %s dist=%.2f%% rsi=%.1f",
                symbol, direction.value, distance_pct * 100, last_rsi)
    return sig


def scan(
    symbols: list,
    api_key: str,
    away_pct: float = _AWAY_PCT,
    sideways_drift_pct: float = _SIDEWAYS_DRIFT_PCT,
    sideways_lookback: int = _SIDEWAYS_LOOKBACK,
    rsi_min: float = _RSI_MIN,
    rsi_max: float = _RSI_MAX,
) -> tuple:
    """
    Scan all symbols. Returns (results, auth_error).
    auth_error=True means Kite token expired — caller should pause.
    """
    results = []
    for symbol, exchange in symbols:
        try:
            sig = scan_one(symbol, exchange, api_key, away_pct, sideways_drift_pct,
                           sideways_lookback, rsi_min, rsi_max)
            if sig:
                results.append(sig)
        except AuthError as exc:
            logger.error("Auth error during Setup A scan: %s", exc)
            return results, True
        except Exception as exc:
            logger.error("Unexpected error scanning %s: %s", symbol, exc)
    return results, False
