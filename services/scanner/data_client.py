"""
Data client — wraps OpenAlgo history_service with:
  - 30-minute in-process cache for daily bars
  - Transparent 401/403 error surfacing
  - Passes through the existing 3 req/sec rate limiter in history_service
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import pytz

from services.history_service import get_history
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_cache: dict[str, Tuple[pd.DataFrame, float]] = {}
_cache_lock = threading.Lock()
_DAILY_CACHE_TTL = 30 * 60  # 30 minutes in seconds


class DataFetchError(Exception):
    pass


class AuthError(DataFetchError):
    pass


def _cache_key(symbol: str, exchange: str, interval: str, start: str, end: str) -> str:
    return f"{symbol}:{exchange}:{interval}:{start}:{end}"


def _get_cached(key: str) -> Optional[pd.DataFrame]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        df, expires_at = entry
        if time.monotonic() > expires_at:
            del _cache[key]
            return None
        return df


def _set_cached(key: str, df: pd.DataFrame, ttl: float = _DAILY_CACHE_TTL) -> None:
    with _cache_lock:
        _cache[key] = (df, time.monotonic() + ttl)


def fetch_ohlc(
    symbol: str,
    exchange: str,
    interval: str,
    from_dt: datetime,
    to_dt: datetime,
    api_key: str,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLC bars from broker via OpenAlgo history_service.
    Returns a DataFrame with columns: timestamp, open, high, low, close, volume, oi.
    timestamp column is datetime (IST-aware for daily, naive-UTC for intraday).
    Raises DataFetchError / AuthError on failure.
    """
    start_str = from_dt.strftime("%Y-%m-%d")
    end_str = to_dt.strftime("%Y-%m-%d")
    key = _cache_key(symbol, exchange, interval, start_str, end_str)

    is_daily = interval in ("D", "day", "1d")

    if use_cache and is_daily:
        cached = _get_cached(key)
        if cached is not None:
            logger.debug("Cache HIT %s %s %s", symbol, interval, start_str)
            return cached

    t0 = time.monotonic()
    success, resp, status = get_history(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start_date=start_str,
        end_date=end_str,
        api_key=api_key,
    )
    elapsed = (time.monotonic() - t0) * 1000

    if not success or status in (401, 403):
        msg = resp.get("message", "unknown error") if isinstance(resp, dict) else str(resp)
        logger.warning("Auth/permission error fetching %s: %s (status=%s)", symbol, msg, status)
        raise AuthError(f"{symbol}: {msg}")

    if not success:
        msg = resp.get("message", "unknown error") if isinstance(resp, dict) else str(resp)
        logger.error("Fetch error %s: %s", symbol, msg)
        raise DataFetchError(f"{symbol}: {msg}")

    records = resp.get("data", [])
    if not records:
        logger.debug("Empty data %s %s %s-%s (%.0fms)", symbol, interval, start_str, end_str, elapsed)
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    df = pd.DataFrame(records)

    # Normalise timestamp to datetime
    if "timestamp" in df.columns:
        if pd.api.types.is_integer_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    else:
        df["timestamp"] = pd.NaT

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("timestamp").reset_index(drop=True)
    logger.debug("Fetch OK %s %s %d bars (%.0fms)", symbol, interval, len(df), elapsed)

    if is_daily and use_cache:
        _set_cached(key, df)

    return df


def clear_daily_cache() -> None:
    with _cache_lock:
        _cache.clear()


def get_daily_bars(
    symbol: str,
    exchange: str,
    lookback_days: int,
    api_key: str,
) -> pd.DataFrame:
    """Convenience: fetch the last `lookback_days` daily bars up to today."""
    today = datetime.now(IST).date()
    from_dt = datetime.combine(today - timedelta(days=lookback_days + 10), datetime.min.time())
    to_dt = datetime.combine(today, datetime.min.time())
    return fetch_ohlc(symbol, exchange, "D", from_dt, to_dt, api_key)


def get_intraday_bars(
    symbol: str,
    exchange: str,
    interval: str,
    lookback_days: int,
    api_key: str,
) -> pd.DataFrame:
    """Convenience: fetch intraday bars for the last `lookback_days` days."""
    today = datetime.now(IST).date()
    from_dt = datetime.combine(today - timedelta(days=lookback_days), datetime.min.time())
    to_dt = datetime.combine(today, datetime.min.time())
    return fetch_ohlc(symbol, exchange, interval, from_dt, to_dt, api_key, use_cache=False)
