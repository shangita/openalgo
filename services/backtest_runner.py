"""Async backtest job queue — data from Historify, strategy from Python scripts."""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

_BACKTEST_DIR = os.path.join(os.path.dirname(__file__), "..", "backtest")
if _BACKTEST_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_BACKTEST_DIR))

_STRATEGIES_DIR = os.path.join(os.path.dirname(__file__), "..", "strategies", "scripts")
_STRATEGY_CONFIGS = os.path.join(os.path.dirname(__file__), "..", "strategies", "strategy_configs.json")

from services.backtest_log_buffer import bt_logger as logger, clear as clear_logs
from services.backtest_strategies import STRATEGIES

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ─── Dataset listing ──────────────────────────────────────────────────────────

def list_datasets() -> list[dict]:
    """Return all Historify catalog entries."""
    try:
        from services.historify_service import get_data_catalog
        ok, payload, _ = get_data_catalog()
        if not ok:
            return []
        items = payload.get("data", [])
        result = []
        for it in items:
            result.append({
                "key": f"{it['symbol']}|{it['exchange']}|{it['interval']}",
                "symbol": it["symbol"],
                "exchange": it["exchange"],
                "interval": it["interval"],
                "record_count": it.get("record_count", 0),
                "first_date": _ts_to_date(it.get("first_timestamp")),
                "last_date":  _ts_to_date(it.get("last_timestamp")),
            })
        return result
    except Exception as exc:
        logger.warning("list_datasets error: %s", exc)
        return []


def _ts_to_date(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


# ─── Python strategy listing ──────────────────────────────────────────────────

_SKIP_STRATEGIES = {"delta_neutral"}  # options strategies, not suitable for OHLCV backtest


def list_python_strategies() -> list[dict]:
    """Return Python strategy scripts with extracted parameters."""
    configs = {}
    try:
        with open(_STRATEGY_CONFIGS) as f:
            configs = json.load(f)
    except Exception:
        pass

    result = []
    for sid, cfg in configs.items():
        if any(skip in sid for skip in _SKIP_STRATEGIES):
            continue

        fp = cfg.get("file_path", "")
        if not fp:
            continue
        abs_fp = os.path.join(os.path.dirname(_STRATEGY_CONFIGS), "..", fp)
        abs_fp = os.path.normpath(abs_fp)
        if not os.path.exists(abs_fp):
            # try scripts dir
            abs_fp = os.path.join(_STRATEGIES_DIR, os.path.basename(fp))
        if not os.path.exists(abs_fp):
            continue

        bt_type, bt_params, raw_params = _parse_strategy_params(abs_fp)
        result.append({
            "id": sid,
            "name": cfg.get("name", sid),
            "file": os.path.basename(fp),
            "bt_type": bt_type,
            "bt_params": bt_params,
            "key_params": _summarise_params(raw_params),
        })

    return result


def _summarise_params(raw: dict) -> dict:
    """Return a compact human-readable subset of strategy params."""
    keep = ["EMA_FAST", "EMA_SLOW", "EMA_PERIOD", "MA_FAST", "MA_SLOW",
            "RSI_PERIOD", "E01_RSI_PERIOD", "ATR_SL_MULT", "ATR_TP_MULT",
            "ATR_PT_MULT", "SL_ATR_MULT", "TP_ATR_MULT", "INITIAL_SL_MULT"]
    out = {}
    for k in keep:
        if k in raw and isinstance(raw[k], (int, float)):
            out[k] = raw[k]
    return out


# ─── Parameter extraction ─────────────────────────────────────────────────────

def _parse_strategy_params(filepath: str):
    """
    AST-parse a Python strategy file, extract uppercase constants,
    detect backtest strategy type, and map to backtest_strategies params.
    Returns (bt_type, mapped_params, raw_params).
    """
    raw: dict[str, Any] = {}
    try:
        with open(filepath) as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1:
                        try:
                            raw[t.id] = ast.literal_eval(node.value)
                        except Exception:
                            pass
    except Exception as exc:
        logger.warning("Strategy parse error %s: %s", filepath, exc)

    # ── Detect type ────────────────────────────────────────────────────────────
    has_ema_fast = "EMA_FAST" in raw or "EMA_PERIOD" in raw
    has_ma_fast  = "MA_FAST" in raw
    has_rsi_only = "RSI_PERIOD" in raw and not has_ema_fast and not has_ma_fast
    has_crossover = "MA_FAST" in raw and "BREAKOUT_PCT" in raw

    if has_crossover:
        bt_type = "ema_crossover"
    elif has_rsi_only:
        bt_type = "rsi_reversal"
    else:
        bt_type = "ema_pullback"

    # ── Map to backtest strategy params ───────────────────────────────────────
    defaults = dict(STRATEGIES[bt_type]["default_params"])

    if bt_type == "ema_pullback":
        defaults["fast"] = int(raw.get("EMA_FAST") or raw.get("EMA_PERIOD") or defaults["fast"])
        defaults["slow"] = int(raw.get("EMA_SLOW") or defaults["slow"])
        defaults["rsi_period"] = int(raw.get("RSI_PERIOD") or defaults["rsi_period"])
        defaults["rsi_entry"] = int(raw.get("RSI_UPPER") or raw.get("RSI_MAX") or defaults["rsi_entry"])
        defaults["sl_mult"] = float(
            raw.get("ATR_SL_MULT") or raw.get("INITIAL_SL_MULT") or raw.get("SL_ATR_MULT") or defaults["sl_mult"]
        )
        defaults["tp_mult"] = float(
            raw.get("ATR_TP_MULT") or raw.get("ATR_PT_MULT") or raw.get("TP_ATR_MULT") or raw.get("TARGET_MULT") or defaults["tp_mult"]
        )

    elif bt_type == "ema_crossover":
        defaults["fast"] = int(raw.get("MA_FAST") or defaults["fast"])
        defaults["slow"] = int(raw.get("MA_SLOW") or defaults["slow"])
        defaults["sl_mult"] = float(raw.get("ATR_SL_MULT") or defaults["sl_mult"])
        defaults["tp_mult"] = float(raw.get("ATR_PT_MULT") or raw.get("ATR_TP_MULT") or defaults["tp_mult"])

    elif bt_type == "rsi_reversal":
        defaults["rsi_period"] = int(
            raw.get("RSI_PERIOD") or raw.get("E01_RSI_PERIOD") or raw.get("E04_RSI_PERIOD") or defaults["rsi_period"]
        )
        defaults["oversold"] = int(raw.get("RSI_LOWER") or raw.get("RSI_MIN") or defaults["oversold"])
        defaults["overbought"] = int(raw.get("RSI_UPPER") or raw.get("RSI_MAX") or defaults["overbought"])
        defaults["sl_mult"] = float(
            raw.get("SL_ATR_MULT") or raw.get("ATR_SL_MULT") or defaults["sl_mult"]
        )
        defaults["tp_mult"] = float(
            raw.get("TP_ATR_MULT") or raw.get("ATR_TP_MULT") or raw.get("ATR_PT_MULT") or defaults["tp_mult"]
        )

    return bt_type, defaults, raw


# ─── Job helpers ──────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except Exception:
        return 0.0


def _update(job_id: str, **kw) -> None:
    with _jobs_lock:
        _jobs[job_id].update(**kw)


# ─── Job runner ───────────────────────────────────────────────────────────────

def _run_job(job_id: str, dataset_key: str, strategy_id: str) -> None:
    try:
        _update(job_id, status="running")

        # Parse dataset key
        parts = dataset_key.split("|")
        if len(parts) != 3:
            raise ValueError(f"Bad dataset_key: {dataset_key}")
        symbol, exchange, interval = parts
        logger.info("Job %s  dataset=%s  strategy=%s", job_id[:8], dataset_key, strategy_id)

        # Locate strategy file
        configs = {}
        try:
            with open(_STRATEGY_CONFIGS) as f:
                configs = json.load(f)
        except Exception:
            pass

        cfg = configs.get(strategy_id, {})
        fp = cfg.get("file_path", "")
        abs_fp = ""
        if fp:
            abs_fp = os.path.normpath(os.path.join(os.path.dirname(_STRATEGY_CONFIGS), "..", fp))
        if not abs_fp or not os.path.exists(abs_fp):
            abs_fp = os.path.join(_STRATEGIES_DIR, f"{strategy_id}.py")
        if not os.path.exists(abs_fp):
            raise FileNotFoundError(f"Strategy file not found for: {strategy_id}")

        logger.info("Strategy file: %s", os.path.basename(abs_fp))
        bt_type, bt_params, raw_params = _parse_strategy_params(abs_fp)
        logger.info("Detected type: %s  params: %s", bt_type, bt_params)

        # Fetch full dataset from Historify DuckDB
        logger.info("Loading full dataset from Historify (%s %s %s) ...", symbol, exchange, interval)
        from database.historify_db import get_ohlcv
        df = get_ohlcv(symbol, exchange, interval)

        if df is None or df.empty:
            raise RuntimeError(f"No data in Historify for {symbol} {exchange} {interval}")

        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
                df = df.set_index("timestamp")
            else:
                df.index = pd.to_datetime(df.index)

        df = df.sort_index()
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        logger.info("Loaded %d bars  %s to %s", len(df), df.index[0].date(), df.index[-1].date())

        if len(df) < 50:
            raise RuntimeError(f"Insufficient data: {len(df)} bars (need >= 50)")

        # Build engine
        from engine import ParamBacktestEngine
        FREQ_MAP = {"D": "1D", "1h": "1H", "5m": "5T", "15m": "15T", "30m": "30T", "1m": "1T"}
        freq = FREQ_MAP.get(interval, "1D")
        engine = ParamBacktestEngine(instrument=f"{symbol}_{exchange}", freq=freq)
        engine.data = df

        # Run signals
        strat_fn = STRATEGIES[bt_type]["fn"]
        logger.info("Running %s signals ...", STRATEGIES[bt_type]["label"])
        result6 = strat_fn(df, **bt_params)
        entries  = result6[0]
        exits    = result6[1]
        sl_stop  = result6[2]
        tp_stop  = result6[3]
        short_entries = result6[4] if len(result6) > 4 else None
        short_exits   = result6[5] if len(result6) > 5 else None

        def _wfa_fn(slice_df, sl_mult, tp_mult):
            p = {**bt_params, "sl_mult": sl_mult, "tp_mult": tp_mult}
            return strat_fn(slice_df, **p)

        engine.set_strategy(_wfa_fn)
        engine.run(entries=entries, exits=exits, sl_stop=sl_stop, tp_stop=tp_stop,
                   short_entries=short_entries, short_exits=short_exits)
        logger.info("Full-sample run complete — %d trades", len(engine.portfolio.trades))

        logger.info("Walk-forward analysis (6 windows) ...")
        try:
            engine.walk_forward(n_splits=6, train_frac=0.7)
            logger.info("WFA done — %d windows", len(engine._wfa_results))
        except Exception as exc:
            logger.warning("WFA skipped: %s", exc)

        logger.info("Monte Carlo simulation (2000 runs) ...")
        try:
            engine.monte_carlo(n_sims=2000)
        except Exception as exc:
            logger.warning("Monte Carlo skipped: %s", exc)

        logger.info("Parameter sensitivity ...")
        try:
            engine.param_sensitivity()
        except Exception as exc:
            logger.warning("Param sensitivity skipped: %s", exc)

        sc = engine.scorecard()
        logger.info("Scorecard: %d/10 passed — %s", sc["pass_count"], sc["verdict"])

        def _clean(v):
            if isinstance(v, float) and not np.isfinite(v):
                return None
            if isinstance(v, np.integer):
                return int(v)
            if isinstance(v, np.floating):
                return float(v)
            return v

        sc_clean = {}
        for k, v in sc.items():
            if isinstance(v, dict):
                sc_clean[k] = {ck: bool(cv) for ck, cv in v.items()}
            else:
                sc_clean[k] = _clean(v)

        equity = engine.portfolio.value()
        equity_pts = [
            {"t": int(ts.timestamp() * 1000), "v": round(_safe_float(v), 2)}
            for ts, v in equity.items()
        ]

        trades = []
        try:
            td = engine.portfolio.trades.records_readable
            for _, row in td.iterrows():
                trades.append({
                    "entry_time":  str(row.get("Entry Timestamp", ""))[:16],
                    "exit_time":   str(row.get("Exit Timestamp", ""))[:16],
                    "direction":   str(row.get("Direction", "")).replace("Direction.", ""),
                    "entry_price": round(_safe_float(row.get("Avg Entry Price", 0)), 2),
                    "exit_price":  round(_safe_float(row.get("Avg Exit Price", 0)), 2),
                    "pnl":         round(_safe_float(row.get("PnL", 0)), 2),
                    "return_pct":  round(_safe_float(row.get("Return", 0)) * 100, 2),
                })
        except Exception as exc:
            logger.warning("Trade list unavailable: %s", exc)

        wfa = []
        for w in (engine._wfa_results or []):
            wfa.append({
                "window":     w.get("window", ""),
                "is_sharpe":  round(_safe_float(w.get("is_sharpe", 0)), 2),
                "oos_sharpe": round(_safe_float(w.get("oos_sharpe", 0)), 2),
                "oos_return": round(_safe_float(w.get("oos_return", 0)) * 100, 2),
                "oos_trades": int(w.get("oos_trades", 0) or 0),
                "profitable": bool(w.get("profitable", False)),
            })

        result = {
            "scorecard": sc_clean,
            "equity_curve": equity_pts,
            "trades": trades,
            "wfa_windows": wfa,
            "total_bars": len(df),
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "strategy_name": cfg.get("name", strategy_id),
            "bt_type": STRATEGIES[bt_type]["label"],
            "bt_params": {k: v for k, v in bt_params.items() if isinstance(v, (int, float))},
        }

        _update(job_id, status="done", result=result,
                finished_at=datetime.utcnow().isoformat())
        logger.info("Job %s complete", job_id[:8])

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id[:8], exc, exc_info=True)
        _update(job_id, status="error", error=str(exc),
                finished_at=datetime.utcnow().isoformat())


def submit_job(dataset_key: str, strategy_id: str) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued",
            "dataset_key": dataset_key, "strategy_id": strategy_id,
            "result": None, "error": None,
            "submitted_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        }

    clear_logs()
    t = threading.Thread(
        target=_run_job, args=(job_id, dataset_key, strategy_id),
        daemon=True, name=f"bt-{job_id[:8]}",
    )
    t.start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None
