"""
Scanner blueprint — all /scanner/* routes.
Integrated with OpenAlgo conventions: check_session_validity, jsonify.
"""
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview, get_auth_token
from services.scanner import store, notifier
from services.scanner.paper_engine import (
    get_positions, is_engine_running, manual_close, open_position, start_engine,
)
from services.scanner.scheduler import get_scheduler
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

scanner_bp = Blueprint("scanner_bp", __name__, url_prefix="/")

_HOST_URL = os.getenv("HOST_SERVER", "http://127.0.0.1:5000").rstrip("/")


def _api_key() -> str | None:
    username = session.get("user")
    if not username:
        return None
    return get_api_key_for_tradingview(username)


def _ok(data=None, **kwargs) -> tuple:
    resp = {"ok": True, "error": None}
    if data is not None:
        resp["data"] = data
    resp.update(kwargs)
    return jsonify(resp), 200


def _err(message: str, code: int = 400) -> tuple:
    return jsonify({"ok": False, "error": message, "data": None}), code

# ─── One-shot scan ────────────────────────────────────────────────────────────

@scanner_bp.route("/scanner/run-once", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_run_once():
    api_key = _api_key()
    if not api_key:
        return _err("API key not configured", 401)

    from services.scanner.scanner_a import scan
    from services.scanner.universe import get_universe

    symbols = get_universe("nifty50")
    try:
        results, auth_err = scan(symbols, api_key)
    except Exception as exc:
        logger.error("run-once scan error: %s", exc)
        return _err(str(exc), 500)

    if auth_err:
        return _err("Kite auth error — please re-login", 403)

    data = []
    for sig in results:
        store.save_signal(sig)
        data.append({
            "signal_id": sig.signal_id,
            "symbol": sig.symbol,
            "setup_id": sig.setup_id.value,
            "direction": sig.direction.value,
            "ltp": sig.ltp,
            "ema5": sig.ema5,
            "rsi14": sig.rsi14,
            "target": sig.target,
            "slope_pct": sig.slope_pct,
            "distance_pct": sig.distance_pct,
            "signal_time": sig.signal_time.isoformat(),
        })

    return _ok(data)


# ─── Continuous scanner ───────────────────────────────────────────────────────

@scanner_bp.route("/scanner/continuous/start", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_continuous_start():
    api_key = _api_key()
    if not api_key:
        return _err("API key not configured", 401)

    scheduler = get_scheduler()
    ok, err_msg = scheduler.start(api_key=api_key, host_url=_HOST_URL)
    if not ok:
        return _err(err_msg, 409)

    # Start paper engine with same api_key
    start_engine(api_key)

    return _ok({"message": "Continuous scanner started"})


@scanner_bp.route("/scanner/continuous/stop", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_continuous_stop():
    get_scheduler().stop()
    return _ok({"message": "Continuous scanner stopped"})


@scanner_bp.route("/scanner/continuous/status", methods=["GET"])
@cross_origin()
@check_session_validity
def scanner_continuous_status():
    return _ok(get_scheduler().status())


# ─── Signals feed ─────────────────────────────────────────────────────────────

@scanner_bp.route("/scanner/signals", methods=["GET"])
@cross_origin()
@check_session_validity
def scanner_signals():
    setup_filter = request.args.get("setup")
    direction_filter = request.args.get("direction")

    signals = store.get_signals_today()

    if setup_filter and setup_filter in ("A", "B"):
        signals = [s for s in signals if s["setup_id"] == setup_filter]
    if direction_filter and direction_filter in ("LONG", "SHORT"):
        signals = [s for s in signals if s["direction"] == direction_filter]

    return _ok(signals)


# ─── Chart data endpoint ──────────────────────────────────────────────────────

@scanner_bp.route("/scanner/chart-data", methods=["GET"])
@cross_origin()
@check_session_validity
def scanner_chart_data():
    symbol = request.args.get("symbol", "").strip()
    exchange = request.args.get("exchange", "NSE").strip()
    setup_id = request.args.get("setup", "A").strip()

    if not symbol:
        return _err("symbol required")

    api_key = _api_key()
    if not api_key:
        return _err("API key not configured", 401)

    from services.scanner.data_client import get_daily_bars, get_intraday_bars
    from services.scanner.indicators import ema

    try:
        daily = get_daily_bars(symbol, exchange, 30, api_key)
    except Exception as exc:
        return _err(str(exc), 500)

    if daily.empty:
        return _err("No data available")

    close = daily["close"]
    ema5 = ema(close, 5)

    # Last 6 daily candles
    last6 = daily.tail(6)
    ema5_last6 = ema5.tail(6)

    daily_candles = []
    for i, row in last6.iterrows():
        ts = row["timestamp"]
        ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else 0
        daily_candles.append({
            "time": ts_ms,
            "open": row["open"], "high": row["high"],
            "low": row["low"], "close": row["close"],
        })

    ema5_line = []
    for ts, val in zip(last6["timestamp"], ema5_last6):
        ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else 0
        ema5_line.append({"time": ts_ms, "value": round(float(val), 2)})

    result = {
        "daily_candles": daily_candles,
        "ema5_line": ema5_line,
    }

    # Setup B: add PDH/PDL lines + last 30 5-min bars
    if setup_id == "B":
        try:
            intra = get_intraday_bars(symbol, exchange, "5m", 3, api_key)
            if not intra.empty:
                intra30 = intra.tail(30)
                intra_candles = []
                for _, row in intra30.iterrows():
                    ts = row["timestamp"]
                    ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else 0
                    intra_candles.append({
                        "time": ts_ms,
                        "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"],
                    })
                result["intra_candles"] = intra_candles
        except Exception:
            pass

        if len(daily) >= 2:
            pdh = float(daily["high"].iloc[-2])
            pdl = float(daily["low"].iloc[-2])
            result["pdh"] = pdh
            result["pdl"] = pdl

    return _ok(result)


# ─── Paper trading ────────────────────────────────────────────────────────────

@scanner_bp.route("/scanner/paper/start", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_paper_start():
    api_key = _api_key()
    if not api_key:
        return _err("API key not configured", 401)

    body = request.get_json(silent=True) or {}
    signal_ids = body.get("signals", [])
    if not signal_ids:
        return _err("signals list required")

    opened = []
    for sid in signal_ids:
        sig_data = store.get_signal_by_id(sid)
        if not sig_data:
            logger.warning("Signal not found: %s", sid)
            continue

        from services.scanner.models import Direction, SetupID
        pos = open_position(
            signal_id=sid,
            symbol=sig_data["symbol"],
            exchange=sig_data["exchange"],
            setup_id=SetupID(sig_data["setup_id"]),
            direction=Direction(sig_data["direction"]),
            target=sig_data["target"],
            api_key=api_key,
        )
        if pos:
            opened.append(pos.position_id)

    # Ensure engine is running
    start_engine(api_key)
    return _ok({"opened": opened})


@scanner_bp.route("/scanner/paper/status", methods=["GET"])
@cross_origin()
@check_session_validity
def scanner_paper_status():
    # Auto-restart engine after service restart if open positions exist and engine is dead
    if not is_engine_running():
        login_username = session.get("user")
        if login_username:
            from database.auth_db import get_api_key_for_tradingview
            api_key = get_api_key_for_tradingview(login_username)
            if api_key and store.get_open_positions():
                start_engine(api_key)

    positions = get_positions()

    open_pos = [p for p in positions if p["status"] in ("OPEN", "DATA_STALLED")]
    closed_pos = [p for p in positions if p["status"] == "CLOSED"]

    total_pnl = sum(p["pnl"] or 0 for p in positions)
    wins = sum(1 for p in closed_pos if (p["pnl"] or 0) > 0)
    win_rate = round(wins / len(closed_pos) * 100, 1) if closed_pos else 0

    setup_a_pnl = sum(p["pnl"] or 0 for p in positions if p["setup_id"] == "A")
    setup_b_pnl = sum(p["pnl"] or 0 for p in positions if p["setup_id"] == "B")

    return _ok({
        "open": open_pos,
        "closed": closed_pos,
        "summary": {
            "total_pnl": round(total_pnl, 2),
            "win_rate": win_rate,
            "setup_a_pnl": round(setup_a_pnl, 2),
            "setup_b_pnl": round(setup_b_pnl, 2),
            "total_trades": len(closed_pos),
        },
    })


@scanner_bp.route("/scanner/paper/stop", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_paper_stop():
    body = request.get_json(silent=True) or {}
    position_id = body.get("position_id", "")
    if not position_id:
        return _err("position_id required")
    ok = manual_close(position_id)
    if not ok:
        return _err("Position not found or already closed", 404)
    return _ok({"message": "Position closed"})


# ─── Telegram test ────────────────────────────────────────────────────────────

@scanner_bp.route("/scanner/telegram/test", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_telegram_test():
    ok = notifier.send(
        "🔔 <b>OpenAlgo Scanner</b> — Test message\nTelegram integration is working correctly."
    )
    if ok:
        return _ok({"message": "Test message sent"})
    return _err("Failed to send — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars", 500)


# ─── Live log feed ────────────────────────────────────────────────────────────

@scanner_bp.route("/scanner/logs", methods=["GET"])
@cross_origin()
@check_session_validity
def scanner_get_logs():
    from services.scanner.log_buffer import current_seq, get_logs
    since = int(request.args.get("since", 0))
    logs = get_logs(since)
    return _ok({"logs": logs, "seq": current_seq()})


@scanner_bp.route("/scanner/logs/clear", methods=["POST"])
@cross_origin()
@check_session_validity
def scanner_clear_logs():
    from services.scanner.log_buffer import clear
    clear()
    return _ok({"message": "Logs cleared"})
