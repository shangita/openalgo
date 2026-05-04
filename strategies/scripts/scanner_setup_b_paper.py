"""
Scanner Setup B — EMA Trend Breakout | Paper Trading
PARAM Capital Strategy | OpenAlgo Python Strategy Format

Setup B Logic:
  Daily candidate filter (every 30 min):
    B1. |close - EMA5| / EMA5 <= 1%       (price hugging EMA)
    B2. |EMA5[t] - EMA5[t-10]| / EMA5[t-10] >= 0.5%  (directional slope)
    B3. 30 <= RSI14 <= 70                  (not exhausted)
    B5. Direction aligned with EMA slope

  5-min breakout trigger (every 1 min, after 09:30):
    B4. LONG: 5-min close > PDH  |  SHORT: 5-min close < PDL

  Target: PDH + (PDH - EMA5) * 1.5  [LONG]
          PDL - (EMA5 - PDL) * 1.5  [SHORT]
  SL: 1.5x ATR14 (5-min), trail at 1.0x ATR14 per bar
  Square-off: 15:15 IST
"""

import os
import math
import time
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from openalgo import api

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SetupB] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner_setup_b")

# ── Config ─────────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

API_KEY = os.getenv("OPENALGO_APIKEY")
if not API_KEY:
    logger.error("OPENALGO_APIKEY not set")
    raise SystemExit(1)

client = api(api_key=API_KEY, host="http://127.0.0.1:5001")

STRATEGY_NAME   = "ScannerSetupB_Paper"
EXCHANGE        = "NSE"
PRODUCT         = "MIS"

# Setup B thresholds
NEAR_PCT              = 0.01    # B1: within 1% of EMA5
DIRECTIONAL_DRIFT_PCT = 0.005   # B2: slope >= 0.5%/10 bars
RSI_MIN               = 30.0    # B3
RSI_MAX               = 70.0
EMA_PERIOD            = 5
RSI_PERIOD            = 14
ATR_PERIOD            = 14
DAILY_LOOKBACK        = 30
TARGET_MULT           = 1.5     # measured-move multiplier

# Skip breakout signals before this time
BREAKOUT_SKIP_UNTIL_H = 9
BREAKOUT_SKIP_UNTIL_M = 30

# Risk
INITIAL_SL_MULT = 1.5
TRAIL_SL_MULT   = 1.0
NOTIONAL        = 100_000

# Timing
CANDIDATE_REFRESH_MIN = 30    # rebuild candidate list every 30 min
BREAKOUT_CHECK_SEC    = 60    # check breakout every 60s
POSITION_POLL_SEC     = 60
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

# ── State ──────────────────────────────────────────────────────────────────────
# candidates: [{symbol, direction, ema5, pdh, pdl, target, slope_pct}]
_candidates: list = []
_positions:  dict = {}   # {symbol: {direction, entry, qty, sl, target, opened}}
_alerted_today: set = set()
_lock = threading.Lock()


# ── Indicators ─────────────────────────────────────────────────────────────────

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
    prev = close.shift(1)
    tr   = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
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
        logger.warning("Daily fetch %s: %s", symbol, e)
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
        logger.warning("5min fetch %s: %s", symbol, e)
    return pd.DataFrame()


# ── Setup B candidate filter ───────────────────────────────────────────────────

def _build_candidates() -> list:
    candidates = []
    for symbol in NIFTY50:
        with _lock:
            if symbol in _positions:
                continue

        df = _fetch_daily(symbol)
        if df.empty or len(df) < 15:
            continue

        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        ema5  = _ema(close, EMA_PERIOD)
        rsi14 = _rsi(close, RSI_PERIOD)

        ltp       = float(close.iloc[-1])
        last_ema5 = float(ema5.iloc[-1])
        last_rsi  = float(rsi14.iloc[-1])

        if last_ema5 <= 0 or math.isnan(last_rsi):
            continue

        # B1: price near EMA5
        dist = abs(ltp - last_ema5) / last_ema5
        if dist > NEAR_PCT:
            continue

        # B2: directional slope
        if len(ema5) < 12:
            continue
        ema_then = float(ema5.iloc[-11])
        if ema_then <= 0:
            continue
        slope = (float(ema5.iloc[-1]) - ema_then) / ema_then
        if abs(slope) < DIRECTIONAL_DRIFT_PCT:
            continue

        # B3: RSI not extreme
        if not (RSI_MIN <= last_rsi <= RSI_MAX):
            continue

        # B5: direction aligned with slope
        direction = "LONG" if slope > 0 else "SHORT"

        # PDH / PDL from second-to-last daily bar
        if len(df) < 2:
            continue
        pdh = float(high.iloc[-2])
        pdl = float(low.iloc[-2])

        # Target
        if direction == "LONG":
            target = pdh + (pdh - last_ema5) * TARGET_MULT
        else:
            target = pdl - (last_ema5 - pdl) * TARGET_MULT

        candidates.append({
            "symbol":    symbol,
            "direction": direction,
            "ema5":      last_ema5,
            "slope_pct": round(slope * 100, 3),
            "rsi":       last_rsi,
            "pdh":       pdh,
            "pdl":       pdl,
            "target":    target,
        })
        logger.debug("Candidate: %s %s slope=%.3f%%", symbol, direction, slope * 100)

    logger.info("Candidates refreshed: %d eligible symbols", len(candidates))
    return candidates


# ── Breakout trigger (B4) ──────────────────────────────────────────────────────

def _check_breakouts(now: datetime) -> list:
    """Check each candidate for a 5-min close beyond PDH/PDL."""
    skip_until = now.replace(
        hour=BREAKOUT_SKIP_UNTIL_H, minute=BREAKOUT_SKIP_UNTIL_M, second=0, microsecond=0
    )
    if now < skip_until:
        return []

    today_str = now.strftime("%Y-%m-%d")
    triggered = []

    with _lock:
        cands = list(_candidates)

    for cand in cands:
        symbol = cand["symbol"]
        dedup_key = f"{today_str}:{symbol}"

        with _lock:
            if dedup_key in _alerted_today:
                continue
            if symbol in _positions:
                continue

        df5 = _fetch_5min(symbol)
        if df5.empty or len(df5) < 3:
            continue

        last_closed_bar = df5.iloc[-2]
        bar_close = float(last_closed_bar["close"])

        # B4 trigger
        if cand["direction"] == "LONG"  and bar_close <= cand["pdh"]:
            continue
        if cand["direction"] == "SHORT" and bar_close >= cand["pdl"]:
            continue

        level = cand["pdh"] if cand["direction"] == "LONG" else cand["pdl"]
        logger.info("BREAKOUT: %s %s bar_close=%.2f > PDH/PDL=%.2f",
                    symbol, cand["direction"], bar_close, level)
        triggered.append({**cand, "ltp": bar_close})

    return triggered


# ── Position open / update / close ─────────────────────────────────────────────

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

    sl = (ltp - INITIAL_SL_MULT * curr_atr) if direction == "LONG" \
         else (ltp + INITIAL_SL_MULT * curr_atr)

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

    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    with _lock:
        _positions[symbol] = {
            "direction": direction,
            "entry":     ltp,
            "qty":       qty,
            "sl":        sl,
            "target":    target,
            "pdh":       sig.get("pdh"),
            "pdl":       sig.get("pdl"),
            "opened":    datetime.now(IST),
        }
        _alerted_today.add(f"{today_str}:{symbol}")


def _update_positions() -> None:
    now = datetime.now(IST)
    squareoff_time = now.replace(hour=SQUAREOFF_H, minute=SQUAREOFF_M, second=0, microsecond=0)

    with _lock:
        symbols = list(_positions.keys())

    for symbol in symbols:
        with _lock:
            pos = _positions.get(symbol)
        if not pos:
            continue

        if now >= squareoff_time:
            _close_position(symbol, pos, reason="EOD")
            continue

        df5 = _fetch_5min(symbol)
        if df5.empty or len(df5) < 3:
            continue

        bar     = df5.iloc[-2]
        cmp     = float(bar["close"])
        curr_atr = float(_atr(df5["high"], df5["low"], df5["close"], ATR_PERIOD).iloc[-1])

        with _lock:
            if pos["direction"] == "LONG":
                new_sl = cmp - TRAIL_SL_MULT * curr_atr
                pos["sl"] = max(pos["sl"], new_sl)
            else:
                new_sl = cmp + TRAIL_SL_MULT * curr_atr
                pos["sl"] = min(pos["sl"], new_sl)
            sl        = pos["sl"]
            target    = pos["target"]
            direction = pos["direction"]

        if direction == "LONG"  and cmp >= target:
            _close_position(symbol, pos, reason="TARGET", exit_price=cmp); continue
        if direction == "SHORT" and cmp <= target:
            _close_position(symbol, pos, reason="TARGET", exit_price=cmp); continue
        if direction == "LONG"  and cmp <= sl:
            _close_position(symbol, pos, reason="SL", exit_price=cmp); continue
        if direction == "SHORT" and cmp >= sl:
            _close_position(symbol, pos, reason="SL", exit_price=cmp); continue

        pnl_unreal = (cmp - pos["entry"]) * pos["qty"] * (1 if direction == "LONG" else -1)
        logger.info("Hold %s %s cmp=%.2f sl=%.2f tgt=%.2f unreal_pnl=₹%.0f",
                    symbol, direction, cmp, sl, target, pnl_unreal)


def _close_position(symbol: str, pos: dict, reason: str, exit_price: float = None) -> None:
    qty       = pos["qty"]
    direction = pos["direction"]
    resp = client.placesmartorder(
        strategy=STRATEGY_NAME,
        symbol=symbol,
        action="SELL" if direction == "LONG" else "BUY",
        exchange=EXCHANGE,
        price_type="MARKET",
        product=PRODUCT,
        quantity=qty,
        position_size=0,
    )
    exit_p = exit_price or pos["entry"]
    pnl    = (exit_p - pos["entry"]) * qty * (1 if direction == "LONG" else -1)
    logger.info("CLOSED %s %s reason=%s exit=%.2f pnl=₹%.0f | resp=%s",
                symbol, direction, reason, exit_p, pnl, resp)

    with _lock:
        _positions.pop(symbol, None)


# ── Market hours ───────────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
    return open_t <= now <= close_t


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    logger.info("=== Scanner Setup B (EMA Breakout) Paper Trading started ===")
    logger.info("Universe: %d symbols | Notional: ₹%s | Target mult: %.1fx",
                len(NIFTY50), f"{NOTIONAL:,}", TARGET_MULT)

    last_candidate_refresh = None

    while True:
        now = datetime.now(IST)

        if not _is_market_open():
            logger.debug("Market closed — sleeping 60s")
            time.sleep(60)
            continue

        # ── Refresh candidates every 30 min ───────────────────────────────────
        should_refresh = (
            last_candidate_refresh is None or
            (now - last_candidate_refresh).total_seconds() >= CANDIDATE_REFRESH_MIN * 60
        )
        if should_refresh:
            logger.info("Refreshing Setup B candidates…")
            new_cands = _build_candidates()
            with _lock:
                _candidates.clear()
                _candidates.extend(new_cands)
            last_candidate_refresh = now

        # ── Check breakouts every cycle (1 min) ───────────────────────────────
        with _lock:
            num_cands = len(_candidates)
        if num_cands > 0:
            triggers = _check_breakouts(now)
            for sig in triggers:
                _open_position(sig)

        # ── Update open positions ──────────────────────────────────────────────
        if _positions:
            _update_positions()

        # ── Daily reset ────────────────────────────────────────────────────────
        today_str = now.strftime("%Y-%m-%d")
        with _lock:
            stale = {k for k in _alerted_today if not k.startswith(today_str)}
            _alerted_today.difference_update(stale)
            if stale:
                _candidates.clear()  # also clear stale candidates overnight

        time.sleep(BREAKOUT_CHECK_SEC)


if __name__ == "__main__":
    main()
