"""
HFT Regime Monitor Blueprint — PARAM Capital
Live KPI computation from Zerodha WebSocket tick stream (Full/Depth mode).
No simulation — all data from broker.
"""

import json
import math
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path

import numpy as np
import psycopg2
from flask import Blueprint, jsonify, request, send_file, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview
from services.market_data_service import MarketDataService, SubscriberPriority, get_market_data_service
from services.websocket_service import get_websocket_connection
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

regime_bp = Blueprint("regime_bp", __name__, url_prefix="/")

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE      = Path(__file__).parent.parent
STATIC_DIR = _BASE / "static" / "regime"
DB_DIR     = _BASE / "db"
ENROLLED_F = DB_DIR / "regime_enrolled.json"

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_TICKS    = 15000       # ~45 min at 3 ticks/sec
KPI_WINDOW   = 5400        # 30 min of ticks used for KPI computation
SESSION_START_H, SESSION_START_M = 9, 15
SESSION_END_H,   SESSION_END_M   = 15, 30

# Noise / calm thresholds
NOISE_SPREAD_PCT   = 0.06   # spread > 0.06% = noisy
CALM_SPREAD_PCT    = 0.03   # spread < 0.03% = calm
NOISE_IMBALANCE    = 0.40   # |buy-sell|/(buy+sell) > 0.4 = noisy
CALM_IMBALANCE     = 0.25
NOISE_MIN_LTQ      = 10     # last_traded_qty < 10 lots/shares = micro noise
SPIKE_PCT          = 0.15   # LTP move > 0.15% from 20-tick mean = spike

# ── In-memory state ────────────────────────────────────────────────────────────
# symbol_key -> deque of processed tick dicts
_tick_buf: dict[str, deque] = {}

# symbol_key -> latest computed KPI dict
_kpi_state: dict[str, dict] = {}

# symbol_key -> list of tick-level regime labels (for RSI_mkt)
_regime_history: dict[str, deque] = {}

_lock = threading.Lock()

# Subscriber IDs so we can unsubscribe cleanly
_sub_ids: list[int] = []

# Connected WS clients per username
_ws_clients: dict[str, object] = {}

# ── DB helpers ─────────────────────────────────────────────────────────────────
def _pg():
    return psycopg2.connect(
        "postgresql://trader:trader@127.0.0.1:5432/openalgo",
        connect_timeout=3
    )


def get_front_month_futures() -> dict:
    """
    Return {name: {symbol, exchange}} for NIFTY and BANKNIFTY near-month futures.
    Picks the smallest expiry that is >= today.
    """
    today = date.today()
    results = {}

    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
    }

    def parse_expiry(s: str) -> date:
        # "28-APR-26" or "26-MAY-26"
        parts = s.split("-")
        d = int(parts[0])
        m = month_map.get(parts[1].upper(), 1)
        y = 2000 + int(parts[2])
        return date(y, m, d)

    try:
        conn = _pg()
        c = conn.cursor()
        for name in ["NIFTY", "BANKNIFTY"]:
            c.execute(
                "SELECT symbol, expiry FROM symtoken "
                "WHERE symbol LIKE %s AND exchange='NFO' "
                "ORDER BY expiry ASC",
                (f"{name}%FUT",)
            )
            for symbol, expiry_str in c.fetchall():
                try:
                    exp = parse_expiry(expiry_str)
                    if exp >= today:
                        results[name] = {"symbol": symbol, "exchange": "NFO"}
                        break
                except Exception:
                    continue
        conn.close()
    except Exception as e:
        logger.error(f"Regime: DB error finding futures: {e}")
        # Fallback: construct symbol from current date
        import calendar
        today = date.today()
        month_abbr = today.strftime("%b").upper()
        yr = str(today.year)[-2:]
        for name, base in [("NIFTY", "NIFTY"), ("BANKNIFTY", "BANKNIFTY")]:
            # Find last Thursday of month (NSE expiry)
            results[name] = {
                "symbol": f"{base}{yr}{month_abbr}FUT",
                "exchange": "NFO"
            }

    return results


# ── Tick processing ────────────────────────────────────────────────────────────

def _extract_tick_metrics(data: dict) -> dict | None:
    """
    Extract and normalise key metrics from a broker tick.
    Returns None if LTP is missing.
    """
    ltp = (
        data.get("last_price")
        or data.get("last_traded_price")
        or data.get("ltp")
    )
    if not ltp:
        return None

    # Volume / qty
    ltq    = data.get("last_traded_quantity", 0) or 0
    volume = data.get("volume_traded", 0) or data.get("volume", 0) or 0
    buy_qty  = data.get("total_buy_quantity",  0) or 0
    sell_qty = data.get("total_sell_quantity", 0) or 0

    # Bid-ask spread from depth (Full mode)
    spread_pct = 0.0
    best_bid = best_ask = 0.0
    depth = data.get("depth") or {}
    buys  = depth.get("buy",  [])
    sells = depth.get("sell", [])

    if buys and sells:
        best_bid = buys[0].get("price",  0) or 0
        best_ask = sells[0].get("price", 0) or 0
        if best_bid > 0 and best_ask > 0:
            mid = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid * 100

    # Book imbalance from total pending qty
    total_qty = buy_qty + sell_qty
    imbalance = abs(buy_qty - sell_qty) / total_qty if total_qty > 0 else 0.0

    # Depth-based imbalance (more precise — ratio of bid depth to ask depth)
    depth_imbalance = 0.0
    if buys and sells:
        bid_depth = sum(l.get("quantity", 0) for l in buys)
        ask_depth = sum(l.get("quantity", 0) for l in sells)
        total_depth = bid_depth + ask_depth
        depth_imbalance = abs(bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0

    return {
        "ts":              time.time(),
        "ltp":             float(ltp),
        "ltq":             int(ltq),
        "volume":          int(volume),
        "buy_qty":         int(buy_qty),
        "sell_qty":        int(sell_qty),
        "spread_pct":      spread_pct,
        "imbalance":       imbalance,
        "depth_imbalance": depth_imbalance,
        "best_bid":        best_bid,
        "best_ask":        best_ask,
        "has_depth":       bool(buys and sells),
    }


def _on_tick(data: dict):
    """Market data callback — called by MarketDataService on every tick."""
    # Identify symbol from data
    sym_key = data.get("symbol_key") or data.get("key")
    if not sym_key:
        # Try to reconstruct from exchange + symbol fields
        exchange = data.get("exchange") or data.get("source_exchange", "")
        symbol   = data.get("symbol", "")
        if exchange and symbol:
            sym_key = f"{exchange}:{symbol}"
        else:
            return

    metrics = _extract_tick_metrics(data)
    if metrics is None:
        return

    with _lock:
        if sym_key not in _tick_buf:
            _tick_buf[sym_key] = deque(maxlen=MAX_TICKS)
        _tick_buf[sym_key].append(metrics)


# ── KPI computation ────────────────────────────────────────────────────────────

def _compute_kpis(ticks: list[dict], sym_key: str) -> dict | None:
    """
    Compute all 5 KPIs from the processed tick list.
    Returns None if insufficient data.
    """
    n = len(ticks)
    if n < 30:
        return None

    # Use last KPI_WINDOW ticks for per-session metrics
    window = ticks[-KPI_WINDOW:]
    n_w = len(window)

    ltps        = [t["ltp"]          for t in window]
    spreads     = [t["spread_pct"]   for t in window]
    imbalances  = [t["imbalance"]    for t in window]
    ltqs        = [t["ltq"]          for t in window]
    buy_qtys    = [t["buy_qty"]      for t in window]
    sell_qtys   = [t["sell_qty"]     for t in window]
    timestamps  = [t["ts"]           for t in window]
    has_depth   = any(t["has_depth"] for t in window)

    arr_ltp    = np.array(ltps,       dtype=float)
    arr_spread = np.array(spreads,    dtype=float)
    arr_imb    = np.array(imbalances, dtype=float)

    # ── KPI 1: NDP ─────────────────────────────────────────────────────────────
    if has_depth:
        noise_mask = (arr_spread > NOISE_SPREAD_PCT) | (arr_imb > NOISE_IMBALANCE) | (np.array(ltqs) < NOISE_MIN_LTQ)
    else:
        # Fallback: use price volatility when depth unavailable
        rets = np.diff(arr_ltp) / arr_ltp[:-1]
        ret_std = float(np.std(rets)) or 1e-9
        noise_mask_ret = np.abs(rets) > 1.5 * ret_std
        noise_mask = np.concatenate([[False], noise_mask_ret])

    # Use ALL session ticks for NDP (session-level metric)
    session_ticks = ticks
    session_n = len(session_ticks)
    session_noise = sum(
        1 for t in session_ticks
        if t["spread_pct"] > NOISE_SPREAD_PCT
        or t["imbalance"]  > NOISE_IMBALANCE
        or t["ltq"]        < NOISE_MIN_LTQ
    )
    ndp = float(np.clip(session_noise / session_n * 100, 0, 100))

    # ── KPI 2: CPI ─────────────────────────────────────────────────────────────
    if has_depth:
        calm_flags = (arr_spread < CALM_SPREAD_PCT) & (arr_imb < CALM_IMBALANCE)
    else:
        rets = np.diff(arr_ltp) / arr_ltp[:-1]
        ret_std = float(np.std(rets)) or 1e-9
        calm_flags_ret = np.abs(rets) < 0.5 * ret_std
        calm_flags = np.concatenate([[True], calm_flags_ret])

    # Measure duration of each calm episode in minutes
    calm_runs = []
    run_start = None
    for i, (calm, ts) in enumerate(zip(calm_flags, timestamps)):
        if calm and run_start is None:
            run_start = ts
        elif not calm and run_start is not None:
            duration_min = (timestamps[i - 1] - run_start) / 60.0
            calm_runs.append(duration_min)
            run_start = None
    if run_start is not None:
        calm_runs.append((timestamps[-1] - run_start) / 60.0)

    cpi = float(np.clip(np.mean(calm_runs) if calm_runs else 2.0, 2, 45))

    # ── KPI 3: VRT ─────────────────────────────────────────────────────────────
    rolling_mean = np.convolve(arr_ltp, np.ones(20) / 20, mode="same")
    spike_mask = np.abs(arr_ltp - rolling_mean) / rolling_mean > (SPIKE_PCT / 100)
    spike_indices = np.where(spike_mask)[0]

    recovery_times = []
    for idx in spike_indices:
        pre = arr_ltp[idx]
        threshold = abs(pre) * 0.001  # 0.1% recovery threshold
        for j in range(int(idx) + 1, min(int(idx) + 120, n_w)):
            if abs(arr_ltp[j] - pre) <= threshold:
                # Convert ticks to seconds
                rec_secs = timestamps[j] - timestamps[idx]
                recovery_times.append(max(rec_secs, 0.1))
                break
        else:
            recovery_times.append(120.0)  # max 2-min cap

    if recovery_times:
        median_rec = float(np.median(recovery_times))
        vrt = float(np.clip(100 - (median_rec / 120.0) * 100, 5, 95))
    else:
        vrt = 72.0  # No spikes = healthy default

    # ── KPI 4: HR ──────────────────────────────────────────────────────────────
    if has_depth:
        # Use book imbalance directly: decreasing imbalance = calm-restoring
        imb_arr = arr_imb
        imb_diff = np.diff(imb_arr)
        calm_restoring  = int(np.sum(imb_diff < -0.01))   # imbalance shrinking
        noise_expanding = int(np.sum(imb_diff >  0.01))   # imbalance growing
    else:
        # Fallback: price mean-reversion
        recent_mean = float(np.mean(arr_ltp[-min(90, n_w):]))
        diffs = np.diff(arr_ltp)
        devs  = arr_ltp[:-1] - recent_mean
        calm_restoring  = int(np.sum((np.sign(diffs) != np.sign(devs)) & (devs != 0)))
        noise_expanding = int(np.sum((np.sign(diffs) == np.sign(devs)) & (devs != 0)))

    hr = float(np.clip(calm_restoring / max(noise_expanding, 1), 0.2, 4.5))

    # ── KPI 5: RSI_mkt ─────────────────────────────────────────────────────────
    # Classify each 5-min window and count flips
    window_secs = 300  # 5 minutes
    if len(timestamps) >= 2:
        total_elapsed_min = (timestamps[-1] - timestamps[0]) / 60.0
    else:
        total_elapsed_min = 1.0

    regime_per_window = []
    w_start = timestamps[0]
    w_ticks = []
    for t_dict in window:
        if t_dict["ts"] - w_start < window_secs:
            w_ticks.append(t_dict)
        else:
            if w_ticks:
                # Classify this mini-window
                ltp_sub   = np.array([x["ltp"]       for x in w_ticks])
                sprd_sub  = np.array([x["spread_pct"] for x in w_ticks])
                imb_sub   = np.array([x["imbalance"]  for x in w_ticks])
                noise_sub = float(np.mean((sprd_sub > NOISE_SPREAD_PCT) | (imb_sub > NOISE_IMBALANCE)))
                regime_per_window.append("NOISE" if noise_sub > 0.4 else "CALM")
            w_start = t_dict["ts"]
            w_ticks = [t_dict]

    regime_changes = sum(
        1 for i in range(1, len(regime_per_window))
        if regime_per_window[i] != regime_per_window[i - 1]
    )
    rsi_mkt = float(np.clip(
        regime_changes / max(total_elapsed_min, 1.0),
        0.01, 0.25
    ))

    # ── Ancillary live metrics (shown on dashboard) ────────────────────────────
    current = ticks[-1]
    tick_rate = 0.0
    if len(ticks) >= 10:
        recent_ts = [t["ts"] for t in ticks[-10:]]
        elapsed = recent_ts[-1] - recent_ts[0]
        tick_rate = 9.0 / elapsed if elapsed > 0 else 0.0

    return {
        "ndp":        round(ndp,    2),
        "cpi":        round(cpi,    2),
        "vrt":        round(vrt,    2),
        "hr":         round(hr,     3),
        "rsi_mkt":    round(rsi_mkt,4),
        # Live ancillaries
        "spread_pct":      round(current["spread_pct"],   4),
        "imbalance":       round(current["imbalance"],     3),
        "ltp":             round(current["ltp"],           2),
        "best_bid":        round(current["best_bid"],      2),
        "best_ask":        round(current["best_ask"],      2),
        "ltq":             current["ltq"],
        "tick_rate":       round(tick_rate, 2),
        "total_ticks":     len(ticks),
        "window_ticks":    n_w,
        "has_depth":       has_depth,
        "last_tick_ts":    current["ts"],
        "calm_restoring":  calm_restoring,
        "noise_expanding": noise_expanding,
    }


def _regime_from_kpis(k: dict) -> str:
    if k["hr"] < 1.0:
        return "QUIET_RISK"
    if k["ndp"] < 30 and k["cpi"] > 15 and k["vrt"] > 70 and k["hr"] > 2.0 and k["rsi_mkt"] < 0.05:
        return "CALM_STRUCTURAL"
    if k["ndp"] < 40 and k["cpi"] >= 8 and k["cpi"] <= 15 and k["vrt"] > 50:
        return "CALM_TRANSIENT"
    if k["vrt"] < 40 and k["rsi_mkt"] > 0.12:
        return "VOLATILE_ACTIVE"
    if k["ndp"] >= 40 or (k["ndp"] >= 30 and k["rsi_mkt"] > 0.12):
        return "NOISE"
    return "MIXED"


# ── Background KPI compute loop ────────────────────────────────────────────────

def _kpi_loop():
    """Runs every 2 seconds — computes KPIs from tick buffers and caches."""
    while True:
        try:
            with _lock:
                keys = list(_tick_buf.keys())

            for sym_key in keys:
                with _lock:
                    ticks = list(_tick_buf[sym_key])
                if len(ticks) < 30:
                    continue
                try:
                    kpis = _compute_kpis(ticks, sym_key)
                    if kpis:
                        kpis["regime"]     = _regime_from_kpis(kpis)
                        kpis["computed_at"] = time.time()
                        with _lock:
                            _kpi_state[sym_key] = kpis
                except Exception as e:
                    logger.error(f"Regime KPI compute error [{sym_key}]: {e}")
        except Exception as e:
            logger.error(f"Regime KPI loop error: {e}")
        time.sleep(2)


# Start background thread once at module load
_kpi_thread = threading.Thread(target=_kpi_loop, daemon=True, name="regime-kpi")
_kpi_thread.start()


# ── WebSocket subscription ─────────────────────────────────────────────────────

def _subscribe_symbols(username: str) -> dict:
    """Subscribe to NIFTY + BANKNIFTY futures in Full/Depth mode."""
    futures = get_front_month_futures()
    if not futures:
        return {"status": "error", "message": "Could not resolve futures symbols"}

    ok, client, err = get_websocket_connection(username)
    if not ok or client is None:
        return {"status": "error", "message": err or "WebSocket not connected"}

    symbols = [{"symbol": v["symbol"], "exchange": v["exchange"]} for v in futures.values()]
    result  = client.subscribe(symbols, mode="Full")

    # Register tick callback on the global MarketDataService
    svc = get_market_data_service()
    filter_keys = {f"{v['exchange']}:{v['symbol']}" for v in futures.values()}

    sub_id = svc.subscribe_with_priority(
        SubscriberPriority.NORMAL,
        "all",          # receive ltp + quote + depth updates
        _on_tick,
        filter_symbols=filter_keys,
        name="regime_monitor",
    )
    _sub_ids.append(sub_id)

    return {
        "status":  "success",
        "symbols": futures,
        "ws_result": result,
        "subscriber_id": sub_id,
    }


# ── Enrolled strategies persistence ───────────────────────────────────────────

def _load_enrolled() -> list:
    try:
        return json.loads(ENROLLED_F.read_text()) if ENROLLED_F.exists() else []
    except Exception:
        return []


def _save_enrolled(data: list):
    DB_DIR.mkdir(exist_ok=True)
    ENROLLED_F.write_text(json.dumps(data, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@regime_bp.route("/regime")
@check_session_validity
def regime_dashboard():
    html = STATIC_DIR / "dashboard.html"
    if not html.exists():
        return "<h1>Regime dashboard HTML missing from static/regime/</h1>", 404
    return send_file(str(html))


@regime_bp.route("/regime/api/subscribe", methods=["POST"])
@cross_origin()
@check_session_validity
def subscribe():
    """Subscribe to NIFTY + BANKNIFTY futures WebSocket feed."""
    username = session.get("user")
    if not username:
        return jsonify({"status": "error", "message": "not authenticated"}), 401

    result = _subscribe_symbols(username)
    return jsonify(result)


@regime_bp.route("/regime/api/kpis", methods=["GET"])
@cross_origin()
@check_session_validity
def get_kpis():
    """Return latest computed KPIs for all subscribed symbols."""
    with _lock:
        state = {k: dict(v) for k, v in _kpi_state.items()}
        tick_counts = {k: len(v) for k, v in _tick_buf.items()}

    return jsonify({
        "status":      "success",
        "kpis":        state,
        "tick_counts": tick_counts,
        "server_time": time.time(),
    })


@regime_bp.route("/regime/api/status", methods=["GET"])
@cross_origin()
@check_session_validity
def get_status():
    """Return subscription status, tick counts, and symbol info."""
    futures = get_front_month_futures()
    with _lock:
        counts = {k: len(v) for k, v in _tick_buf.items()}
        has_data = bool(_kpi_state)

    return jsonify({
        "status":       "success",
        "futures":      futures,
        "tick_counts":  counts,
        "has_live_data": has_data,
        "subscriber_ids": _sub_ids,
        "server_time":  time.time(),
    })


@regime_bp.route("/regime/api/symbols", methods=["GET"])
@cross_origin()
@check_session_validity
def get_symbols():
    """Return current front-month futures symbols."""
    futures = get_front_month_futures()
    return jsonify({"status": "success", "symbols": futures})


@regime_bp.route("/regime/api/enrolled", methods=["GET"])
@cross_origin()
@check_session_validity
def list_enrolled():
    return jsonify({"status": "success", "data": _load_enrolled()})


@regime_bp.route("/regime/api/enrolled", methods=["POST"])
@cross_origin()
@check_session_validity
def add_enrolled():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:60]
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    entry = {
        "id":         int(time.time() * 1000),
        "name":       name,
        "desc":       (body.get("desc") or "").strip()[:200],
        "instance":   body.get("instance", "paper"),
        "rules":      body.get("rules") or {},
        "auto_mode":  bool(body.get("auto_mode", False)),
        "created_at": datetime.now().isoformat(),
    }
    data = _load_enrolled()
    data.append(entry)
    _save_enrolled(data)
    return jsonify({"status": "success", "data": entry})


@regime_bp.route("/regime/api/enrolled/<int:sid>", methods=["DELETE"])
@cross_origin()
@check_session_validity
def del_enrolled(sid):
    data = [e for e in _load_enrolled() if e.get("id") != sid]
    _save_enrolled(data)
    return jsonify({"status": "success"})
