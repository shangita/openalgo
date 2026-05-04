"""
Scanner Setup A — EMA Sideways Pullback | Paper Trading
PARAM Capital Strategy | OpenAlgo Python Strategy Format

Setup A Logic:
  A1. |close - EMA5| / EMA5 >= 3%   (price far from flat EMA)
  A2. |EMA5[t] - EMA5[t-10]| / EMA5[t-10] <= 0.5%  (EMA sideways)
  A3. 40 <= RSI14 <= 60  (neutral zone)
  Direction: LONG if close < EMA5, SHORT if close > EMA5
  Target: current EMA5 value
  SL: 1.5x ATR14 (5-min), trail at 1.0x ATR14 per bar
  Square-off: 15:15 IST
  Scans: all Nifty 50, every 30 min
"""

import os
import math
import time
import logging
import threading
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from openalgo import api

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SetupA] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner_setup_a")

# ── Config ─────────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

API_KEY = os.getenv("OPENALGO_APIKEY")
if not API_KEY:
    logger.error("OPENALGO_APIKEY not set")
    raise SystemExit(1)

client = api(api_key=API_KEY, host="http://127.0.0.1:5001")

STRATEGY_NAME   = "ScannerSetupA_Paper"
EXCHANGE        = "NSE"
PRODUCT         = "MIS"

# Setup A thresholds
AWAY_PCT        = 0.03    # A1: price at least 3% from EMA5
SIDEWAYS_PCT    = 0.005   # A2: EMA5 drift <= 0.5% over 10 bars
RSI_MIN         = 40.0    # A3
RSI_MAX         = 60.0
EMA_PERIOD      = 5
RSI_PERIOD      = 14
ATR_PERIOD      = 14
DAILY_LOOKBACK  = 30      # days of daily bars to fetch

# Risk
INITIAL_SL_MULT = 1.5
TRAIL_SL_MULT   = 1.0
NOTIONAL        = 100_000  # ₹1 lakh per trade

# Timing
SCAN_INTERVAL_MIN    = 30   # re-scan every 30 min
POSITION_POLL_SEC    = 60   # check positions every 60s
SQUAREOFF_H, SQUAREOFF_M = 15, 15
MARKET_OPEN_H,  MARKET_OPEN_M  = 9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30

# Nifty 50 universe
NIFTY50 = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK","INFY","SBIN",
    "HINDUNILVR","ITC","LT","BAJFINANCE","KOTAKBANK","HCLTECH","MARUTI",
    "AXISBANK","ASIANPAINT","SUNPHARMA","ULTRACEMCO","TITAN","WIPRO",
    "ONGC","NTPC","JSWSTEEL","TATASTEEL","POWERGRID","M&M","BAJAJFINSV",
    "NESTLEIND","TECHM","ADANIENT","ADANIPORTS","COALINDIA","DIVISLAB",
    "DRREDDY","EICHERMOT","GRASIM","HDFCLIFE","HEROMOTOCO","HINDALCO",
    "INDUSINDBK","SBILIFE","SHRIRAMFIN","TATACONSUM","TRENT",
    "BPCL","CIPLA","BRITANNIA","APOLLOHOSP","BEL",
]

# ── In-memory position state ───────────────────────────────────────────────────
# {symbol: {"direction": "LONG"|"SHORT", "entry": float, "qty": int,
#            "sl": float, "target": float, "opened": datetime}}
_positions: dict = {}
_alerted_today: set = set()  # dedup: "YYYY-MM-DD:SYMBOL"
_lock = threading.Lock()


# ── Indicators (Wilder smoothing, no TA-Lib) ───────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    alpha = 1.0 / period
    avg_g = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_l = loss.ewm(alpha=alpha, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev  = close.shift(1)
    tr    = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ── Data helpers ───────────────────────────────────────────────────────────────

def _fetch_daily(symbol: str) -> pd.DataFrame:
    end   = datetime.now(IST).strftime("%Y-%m-%d")
    start = (datetime.now(IST) - timedelta(days=DAILY_LOOKBACK + 10)).strftime("%Y-%m-%d")
    try:
        df = client.history(symbol=symbol, exchange=EXCHANGE, interval="D",
                            start_date=start, end_date=end)
        if isinstance(df, pd.DataFrame) and not df.empty:
            for col in ("open","high","low","close","volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.reset_index(drop=True)
    except Exception as e:
        logger.warning("Daily fetch error %s: %s", symbol, e)
    return pd.DataFrame()


def _fetch_5min(symbol: str) -> pd.DataFrame:
    end   = datetime.now(IST).strftime("%Y-%m-%d")
    start = (datetime.now(IST) - timedelta(days=3)).strftime("%Y-%m-%d")
    try:
        df = client.history(symbol=symbol, exchange=EXCHANGE, interval="5m",
                            start_date=start, end_date=end)
        if isinstance(df, pd.DataFrame) and not df.empty:
            for col in ("open","high","low","close","volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.reset_index(drop=True)
    except Exception as e:
        logger.warning("5min fetch error %s: %s", symbol, e)
    return pd.DataFrame()


# ── Setup A scanner ────────────────────────────────────────────────────────────

def _scan_setup_a() -> list:
    """Return list of qualifying signals: [{symbol, direction, ltp, ema5, rsi, target}]"""
    signals = []
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    for symbol in NIFTY50:
        dedup_key = f"{today_str}:{symbol}"
        with _lock:
            if dedup_key in _alerted_today:
                continue            # already signalled today
            if symbol in _positions:
                continue            # already in a position

        df = _fetch_daily(symbol)
        if df.empty or len(df) < 20:
            continue

        close = df["close"]
        ema5  = _ema(close, EMA_PERIOD)
        rsi14 = _rsi(close, RSI_PERIOD)

        ltp       = float(close.iloc[-1])
        last_ema5 = float(ema5.iloc[-1])
        last_rsi  = float(rsi14.iloc[-1])

        if last_ema5 <= 0 or math.isnan(last_rsi):
            continue

        # A1: price away from EMA5
        dist = abs(ltp - last_ema5) / last_ema5
        if dist < AWAY_PCT:
            continue

        # A2: EMA sideways
        if len(ema5) < 12:
            continue
        ema_now  = float(ema5.iloc[-1])
        ema_then = float(ema5.iloc[-11])
        if ema_then <= 0:
            continue
        slope = abs(ema_now - ema_then) / ema_then
        if slope > SIDEWAYS_PCT:
            continue

        # A3: RSI neutral
        if not (RSI_MIN <= last_rsi <= RSI_MAX):
            continue

        direction = "LONG" if ltp < last_ema5 else "SHORT"
        signals.append({
            "symbol":    symbol,
            "direction": direction,
            "ltp":       ltp,
            "ema5":      last_ema5,
            "rsi":       last_rsi,
            "dist_pct":  round(dist * 100, 2),
            "target":    last_ema5,
        })
        logger.info("Setup A signal: %s %s dist=%.2f%% rsi=%.1f tgt=%.2f",
                    symbol, direction, dist * 100, last_rsi, last_ema5)

    return signals


# ── Position management ────────────────────────────────────────────────────────

def _open_position(sig: dict) -> None:
    symbol    = sig["symbol"]
    direction = sig["direction"]
    ltp       = sig["ltp"]
    target    = sig["target"]

    df5 = _fetch_5min(symbol)
    if df5.empty or len(df5) < ATR_PERIOD + 3:
        logger.warning("Cannot open %s — insufficient 5-min bars", symbol)
        return

    curr_atr = float(_atr(df5["high"], df5["low"], df5["close"], ATR_PERIOD).iloc[-1])
    qty = max(1, math.floor(NOTIONAL / ltp))

    if direction == "LONG":
        sl = ltp - INITIAL_SL_MULT * curr_atr
    else:
        sl = ltp + INITIAL_SL_MULT * curr_atr

    # Place smart order (OpenAlgo manages position sizing)
    pos_size = qty if direction == "LONG" else -qty
    resp = client.placesmartorder(
        strategy=STRATEGY_NAME,
        symbol=symbol,
        action="BUY" if direction == "LONG" else "SELL",
        exchange=EXCHANGE,
        price_type="MARKET",
        product=PRODUCT,
        quantity=qty,
        position_size=pos_size,
    )
    logger.info("Open %s %s qty=%d entry=%.2f sl=%.2f tgt=%.2f | resp=%s",
                symbol, direction, qty, ltp, sl, target, resp)

    with _lock:
        _positions[symbol] = {
            "direction": direction,
            "entry":     ltp,
            "qty":       qty,
            "sl":        sl,
            "target":    target,
            "opened":    datetime.now(IST),
        }
        _alerted_today.add(f"{datetime.now(IST).strftime('%Y-%m-%d')}:{symbol}")


def _update_positions() -> None:
    """Trailing SL + target/SL/EOD exits for all open positions."""
    now = datetime.now(IST)
    squareoff_time = now.replace(hour=SQUAREOFF_H, minute=SQUAREOFF_M, second=0, microsecond=0)

    with _lock:
        symbols = list(_positions.keys())

    for symbol in symbols:
        with _lock:
            pos = _positions.get(symbol)
        if not pos:
            continue

        # EOD square-off
        if now >= squareoff_time:
            _close_position(symbol, pos, reason="EOD")
            continue

        df5 = _fetch_5min(symbol)
        if df5.empty or len(df5) < 3:
            continue

        bar  = df5.iloc[-2]   # last fully closed 5-min bar
        cmp  = float(bar["close"])
        curr_atr = float(_atr(df5["high"], df5["low"], df5["close"], ATR_PERIOD).iloc[-1])

        # Update trailing SL (ratchet only)
        with _lock:
            if pos["direction"] == "LONG":
                new_sl = cmp - TRAIL_SL_MULT * curr_atr
                pos["sl"] = max(pos["sl"], new_sl)
            else:
                new_sl = cmp + TRAIL_SL_MULT * curr_atr
                pos["sl"] = min(pos["sl"], new_sl)
            sl     = pos["sl"]
            target = pos["target"]
            direction = pos["direction"]

        # Target hit
        if direction == "LONG" and cmp >= target:
            _close_position(symbol, pos, reason="TARGET", exit_price=cmp); continue
        if direction == "SHORT" and cmp <= target:
            _close_position(symbol, pos, reason="TARGET", exit_price=cmp); continue

        # SL hit
        if direction == "LONG" and cmp <= sl:
            _close_position(symbol, pos, reason="SL", exit_price=cmp); continue
        if direction == "SHORT" and cmp >= sl:
            _close_position(symbol, pos, reason="SL", exit_price=cmp); continue

        pnl_unreal = (cmp - pos["entry"]) * pos["qty"] * (1 if direction == "LONG" else -1)
        logger.info("Hold %s %s cmp=%.2f sl=%.2f tgt=%.2f unreal_pnl=₹%.0f",
                    symbol, direction, cmp, sl, target, pnl_unreal)


def _close_position(symbol: str, pos: dict, reason: str, exit_price: float = None) -> None:
    qty = pos["qty"]
    direction = pos["direction"]
    exit_action = "SELL" if direction == "LONG" else "BUY"
    close_size = 0  # flat

    resp = client.placesmartorder(
        strategy=STRATEGY_NAME,
        symbol=symbol,
        action=exit_action,
        exchange=EXCHANGE,
        price_type="MARKET",
        product=PRODUCT,
        quantity=qty,
        position_size=close_size,
    )

    exit_p  = exit_price or pos["entry"]
    pnl     = (exit_p - pos["entry"]) * qty * (1 if direction == "LONG" else -1)
    logger.info("CLOSED %s %s reason=%s exit=%.2f pnl=₹%.0f | resp=%s",
                symbol, direction, reason, exit_p, pnl, resp)

    with _lock:
        _positions.pop(symbol, None)


# ── Market hours helper ────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
    return open_t <= now <= close_t


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    logger.info("=== Scanner Setup A (EMA Pullback) Paper Trading started ===")
    logger.info("Universe: %d symbols | Notional: ₹%s | SL: %.1fx ATR | Trail: %.1fx ATR",
                len(NIFTY50), f"{NOTIONAL:,}", INITIAL_SL_MULT, TRAIL_SL_MULT)

    last_scan_time = None

    while True:
        now = datetime.now(IST)

        if not _is_market_open():
            logger.debug("Market closed — sleeping 60s")
            time.sleep(60)
            continue

        # ── Scan every 30 minutes ──────────────────────────────────────────────
        should_scan = (
            last_scan_time is None or
            (now - last_scan_time).total_seconds() >= SCAN_INTERVAL_MIN * 60
        )
        if should_scan:
            logger.info("Running Setup A scan across %d Nifty50 symbols…", len(NIFTY50))
            signals = _scan_setup_a()
            logger.info("Scan complete — %d new signals", len(signals))
            for sig in signals:
                _open_position(sig)
            last_scan_time = now

        # ── Update open positions every 60s ───────────────────────────────────
        if _positions:
            _update_positions()

        # ── Daily reset of alerted set ─────────────────────────────────────────
        today_str = now.strftime("%Y-%m-%d")
        with _lock:
            stale = {k for k in _alerted_today if not k.startswith(today_str)}
            _alerted_today.difference_update(stale)

        time.sleep(POSITION_POLL_SEC)


if __name__ == "__main__":
    main()
