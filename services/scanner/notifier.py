"""
Telegram notifier — direct HTTPS calls, no python-telegram-bot dependency.
Uses httpx (already a dep of OpenAlgo).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

import httpx

from utils.logging import get_logger

logger = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_SEND_RATE_INTERVAL = 1.0  # seconds between sends (conservative)
_last_send: float = 0.0
_rate_lock = threading.Lock()

# In-memory dedupe set for today's alerts: {dedupe_key}
_alerted_today: set[str] = set()
_alerted_lock = threading.Lock()
_alerted_date: Optional[str] = None


def _enforce_rate() -> None:
    global _last_send
    with _rate_lock:
        elapsed = time.monotonic() - _last_send
        if elapsed < _SEND_RATE_INTERVAL:
            time.sleep(_SEND_RATE_INTERVAL - elapsed)
        _last_send = time.monotonic()


def _reset_daily_if_needed() -> None:
    global _alerted_date, _alerted_today
    today = time.strftime("%Y-%m-%d")
    with _alerted_lock:
        if _alerted_date != today:
            _alerted_date = today
            _alerted_today = set()


def is_already_alerted(dedupe_key: str) -> bool:
    _reset_daily_if_needed()
    with _alerted_lock:
        return dedupe_key in _alerted_today


def mark_alerted(dedupe_key: str) -> None:
    _reset_daily_if_needed()
    with _alerted_lock:
        _alerted_today.add(dedupe_key)


def send(text: str, dedupe_key: Optional[str] = None) -> bool:
    """
    Send a Telegram message. Returns True on success, False on any error.
    Never raises — Telegram outages must not crash the scanner.
    If dedupe_key is provided, skips sending if already sent today.
    """
    if dedupe_key and is_already_alerted(dedupe_key):
        logger.debug("Telegram dedupe skip: %s", dedupe_key)
        return False

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False

    parse_mode = os.getenv("TELEGRAM_PARSE_MODE", "HTML")
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"

    _enforce_rate()
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        if resp.status_code == 200:
            if dedupe_key:
                mark_alerted(dedupe_key)
            return True
        logger.warning("Telegram API error %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


# ─── Message builders ──────────────────────────────────────────────────────────

def fmt_setup_a(symbol: str, direction: str, ltp: float, ema5: float,
                distance_pct: float, rsi: float, ts: str, dashboard_url: str) -> str:
    return (
        f"📊 <b>SETUP A — EMA Pullback</b>\n"
        f"<b>{symbol}</b> | {direction}\n"
        f"Date: {ts} IST\n\n"
        f"LTP: ₹{ltp:.2f}\n"
        f"EMA5 (target): ₹{ema5:.2f}\n"
        f"Distance from EMA: {distance_pct:.2f}%\n"
        f"RSI(14): {rsi:.1f}\n\n"
        f"Open in dashboard: {dashboard_url}"
    )


def fmt_setup_b(symbol: str, direction: str, ltp: float, pdh_or_pdl_label: str,
                level: float, ema5: float, slope_pct: float, rsi: float,
                target: float, ts: str, dashboard_url: str) -> str:
    return (
        f"🚀 <b>SETUP B — EMA Trend Breakout</b>\n"
        f"<b>{symbol}</b> | {direction}\n"
        f"Date: {ts} IST\n\n"
        f"LTP: ₹{ltp:.2f} (broke {pdh_or_pdl_label} ₹{level:.2f})\n"
        f"EMA5: ₹{ema5:.2f} (slope {slope_pct:+.2f}%/10d)\n"
        f"RSI(14): {rsi:.1f}\n"
        f"Suggested target: ₹{target:.2f}\n\n"
        f"Open in dashboard: {dashboard_url}"
    )


def fmt_paper_open(symbol: str, direction: str, entry: float, sl: float, tgt: float) -> str:
    return f"✅ {symbol} {direction} entry ₹{entry:.2f} | SL ₹{sl:.2f} | Tgt ₹{tgt:.2f}"


def fmt_paper_target(symbol: str, exit_price: float, pnl: float) -> str:
    return f"🎯 {symbol} target hit @ ₹{exit_price:.2f} | P&L ₹{pnl:+.0f}"


def fmt_paper_sl(symbol: str, exit_price: float, pnl: float) -> str:
    return f"🛑 {symbol} SL hit @ ₹{exit_price:.2f} | P&L ₹{pnl:+.0f}"


def fmt_paper_eod(symbol: str, exit_price: float, pnl: float) -> str:
    return f"⏰ {symbol} EOD square-off @ ₹{exit_price:.2f} | P&L ₹{pnl:+.0f}"


def fmt_paper_data_fail(symbol: str) -> str:
    return f"⚠️ {symbol} DATA_FAIL — forced exit after stall"


def fmt_scanner_started(ts: str) -> str:
    return f"▶️ Continuous scanner started at {ts} IST"


def fmt_scanner_stopped(ts: str) -> str:
    return f"⏹️ Continuous scanner stopped at {ts} IST"


def fmt_scanner_paused() -> str:
    return "⚠️ Scanner paused — Kite auth error, retrying..."


def fmt_scanner_resumed() -> str:
    return "✅ Scanner resumed — Kite auth recovered"
