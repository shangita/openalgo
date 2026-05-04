"""Async backtest job queue — one background thread per job."""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

_BACKTEST_DIR = os.path.join(os.path.dirname(__file__), "..", "backtest")
if _BACKTEST_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_BACKTEST_DIR))

from services.backtest_log_buffer import bt_logger as logger, clear as clear_logs
from services.backtest_strategies import STRATEGIES

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()

INTERVAL_TO_FREQ = {
    "D": "1D", "1h": "1H", "5m": "5T",
    "15m": "15T", "30m": "30T", "1m": "1T",
}


def _safe_float(v) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except Exception:
        return 0.0


def _update(job_id: str, **kw) -> None:
    with _jobs_lock:
        _jobs[job_id].update(**kw)


def _run_job(job_id: str, symbol: str, exchange: str, interval: str,
             start_date: str, end_date: str, strategy_id: str, params: dict) -> None:
    from services.historify_service import get_chart_data

    try:
        _update(job_id, status="running")
        logger.info("Job %s started — %s %s %s  %s to %s  strategy=%s",
                    job_id[:8], symbol, exchange, interval,
                    start_date, end_date, strategy_id)

        # Fetch data
        logger.info("Fetching data from Historify ...")
        ok, payload, _ = get_chart_data(symbol, exchange, interval, start_date, end_date)
        if not ok or payload.get("status") != "success":
            raise RuntimeError(f"Historify data fetch failed: {payload}")

        records = payload.get("data", [])
        if len(records) < 50:
            raise RuntimeError(f"Insufficient bars: {len(records)} (need >= 50)")

        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("timestamp").sort_index()
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        logger.info("Loaded %d bars  %s to %s", len(df),
                    df.index[0].date(), df.index[-1].date())

        # Build engine
        from engine import ParamBacktestEngine
        freq = INTERVAL_TO_FREQ.get(interval, "1D")
        engine = ParamBacktestEngine(instrument=f"{symbol}_{exchange}", freq=freq)
        engine.data = df

        # Run strategy
        strat_meta = STRATEGIES[strategy_id]
        strat_fn = strat_meta["fn"]
        merged = {**strat_meta["default_params"], **params}
        logger.info("Strategy: %s  params: %s", strategy_id, merged)

        result6 = strat_fn(df, **merged)
        entries  = result6[0]
        exits    = result6[1]
        sl_stop  = result6[2]
        tp_stop  = result6[3]
        short_entries = result6[4] if len(result6) > 4 else None
        short_exits   = result6[5] if len(result6) > 5 else None

        # Register strategy for WFA
        def _wfa_fn(slice_df, sl_mult, tp_mult):
            p = {**merged, "sl_mult": sl_mult, "tp_mult": tp_mult}
            return strat_fn(slice_df, **p)

        engine.set_strategy(_wfa_fn)
        engine.run(entries=entries, exits=exits, sl_stop=sl_stop, tp_stop=tp_stop,
                   short_entries=short_entries, short_exits=short_exits)
        logger.info("Full-sample run complete — %d trades", len(engine.portfolio.trades))

        # Walk-forward
        logger.info("Walk-forward analysis (6 windows) ...")
        try:
            engine.walk_forward(n_splits=6, train_frac=0.7)
            logger.info("WFA done — %d windows", len(engine._wfa_results))
        except Exception as exc:
            logger.warning("WFA skipped: %s", exc)

        # Monte Carlo
        logger.info("Monte Carlo simulation (2000 runs) ...")
        try:
            engine.monte_carlo(n_sims=2000)
            logger.info("MC done")
        except Exception as exc:
            logger.warning("Monte Carlo skipped: %s", exc)

        # Param sensitivity
        logger.info("Parameter sensitivity ...")
        try:
            engine.param_sensitivity()
            logger.info("Sensitivity done")
        except Exception as exc:
            logger.warning("Param sensitivity skipped: %s", exc)

        # Scorecard
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

        # Equity curve
        equity = engine.portfolio.value()
        equity_pts = [
            {"t": int(ts.timestamp() * 1000), "v": round(_safe_float(v), 2)}
            for ts, v in equity.items()
        ]

        # Trades
        trades = []
        try:
            td = engine.portfolio.trades.records_readable
            for _, row in td.iterrows():
                trades.append({
                    "entry_time":  str(row.get("Entry Timestamp", "")),
                    "exit_time":   str(row.get("Exit Timestamp", "")),
                    "direction":   str(row.get("Direction", "")),
                    "entry_price": round(_safe_float(row.get("Avg Entry Price", 0)), 2),
                    "exit_price":  round(_safe_float(row.get("Avg Exit Price", 0)), 2),
                    "pnl":         round(_safe_float(row.get("PnL", 0)), 2),
                    "return_pct":  round(_safe_float(row.get("Return", 0)) * 100, 2),
                })
        except Exception as exc:
            logger.warning("Trade list unavailable: %s", exc)

        # WFA windows
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
            "strategy": strategy_id,
        }

        _update(job_id, status="done", result=result,
                finished_at=datetime.utcnow().isoformat())
        logger.info("Job %s complete", job_id[:8])

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id[:8], exc, exc_info=True)
        _update(job_id, status="error", error=str(exc),
                finished_at=datetime.utcnow().isoformat())


def submit_job(symbol: str, exchange: str, interval: str,
               start_date: str, end_date: str,
               strategy_id: str, params: dict) -> str:
    if strategy_id not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_id}")

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued",
            "symbol": symbol, "exchange": exchange,
            "interval": interval, "start_date": start_date, "end_date": end_date,
            "strategy": strategy_id, "params": params,
            "result": None, "error": None,
            "submitted_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        }

    clear_logs()
    t = threading.Thread(
        target=_run_job,
        args=(job_id, symbol, exchange, interval, start_date, end_date, strategy_id, params),
        daemon=True, name=f"bt-{job_id[:8]}",
    )
    t.start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None
