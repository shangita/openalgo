"""
PARAM Capital Backtesting Engine — Scorecard (10-criterion validation)
"""
from __future__ import annotations

import textwrap
from typing import Any

import pandas as pd

from config import SCORECARD_THRESHOLDS


def evaluate(engine_results: dict[str, Any]) -> dict[str, Any]:
    """
    Runs all 10 PARAM Capital scorecard criteria.

    engine_results must contain keys produced by ParamBacktestEngine:
        stats, wfa_results, mc_results, sensitivity_results,
        regime_results, slip2x_stats
    """
    t = SCORECARD_THRESHOLDS
    r = engine_results

    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    # 1. OOS/IS Sharpe ratio
    is_sharpe  = _safe(r, "wfa_results.is_sharpe_mean",  0)
    oos_sharpe = _safe(r, "wfa_results.oos_sharpe_mean", 0)
    ratio = (oos_sharpe / is_sharpe) if is_sharpe > 0 else 0.0
    checks["oos_is_sharpe_ratio"] = ratio >= t["oos_is_sharpe_ratio"]
    details["oos_is_sharpe_ratio"] = f"OOS/IS Sharpe = {ratio:.3f} (≥{t['oos_is_sharpe_ratio']})"

    # 2. Minimum trades
    n_trades = int(_safe(r, "stats.total_trades", 0))
    checks["min_trades"] = n_trades >= t["min_trades"]
    details["min_trades"] = f"Trades = {n_trades} (≥{t['min_trades']})"

    # 3. Profit factor
    pf = _safe(r, "stats.profit_factor", 0)
    checks["min_profit_factor"] = pf >= t["min_profit_factor"]
    details["min_profit_factor"] = f"PF = {pf:.3f} (≥{t['min_profit_factor']})"

    # 4. Calmar ratio
    calmar = _safe(r, "stats.calmar", 0)
    checks["min_calmar"] = calmar >= t["min_calmar"]
    details["min_calmar"] = f"Calmar = {calmar:.3f} (≥{t['min_calmar']})"

    # 5. WFA profitable windows
    profitable_windows = int(_safe(r, "wfa_results.profitable_windows", 0))
    checks["min_wfa_profitable_windows"] = profitable_windows >= t["min_wfa_profitable_windows"]
    details["min_wfa_profitable_windows"] = (
        f"Profitable windows = {profitable_windows}/8 (≥{t['min_wfa_profitable_windows']})"
    )

    # 6. Parameter sensitivity (Sharpe σ/μ)
    sens_cv = _safe(r, "sensitivity_results.sharpe_cv", 1.0)
    checks["max_param_sensitivity"] = sens_cv <= t["max_param_sensitivity"]
    details["max_param_sensitivity"] = f"Sharpe CV = {sens_cv:.3f} (≤{t['max_param_sensitivity']})"

    # 7. Monte Carlo ruin probability
    mc_ruin_pct = _safe(r, "mc_results.ruin_pct", 100.0)
    checks["max_mc_ruin_pct"] = mc_ruin_pct <= t["max_mc_ruin_pct"]
    details["max_mc_ruin_pct"] = f"MC ruin = {mc_ruin_pct:.2f}% (≤{t['max_mc_ruin_pct']}%)"

    # 8. t-statistic of mean daily return
    t_stat = abs(_safe(r, "stats.t_stat", 0))
    checks["min_t_stat"] = t_stat >= t["min_t_stat"]
    details["min_t_stat"] = f"|t-stat| = {t_stat:.3f} (≥{t['min_t_stat']})"

    # 9. Profitable regimes
    profitable_regimes = int(_safe(r, "regime_results.profitable_regimes", 0))
    checks["min_profitable_regimes"] = profitable_regimes >= t["min_profitable_regimes"]
    details["min_profitable_regimes"] = (
        f"Profitable regimes = {profitable_regimes} (≥{t['min_profitable_regimes']})"
    )

    # 10. Sharpe at 2× slippage
    slip2x_sharpe = _safe(r, "slip2x_stats.sharpe", 0)
    checks["min_2x_slip_sharpe"] = slip2x_sharpe >= t["min_2x_slip_sharpe"]
    details["min_2x_slip_sharpe"] = (
        f"2× slip Sharpe = {slip2x_sharpe:.3f} (≥{t['min_2x_slip_sharpe']})"
    )

    pass_count = sum(checks.values())
    verdict = "PASS" if pass_count == len(checks) else (
        "CONDITIONAL" if pass_count >= 7 else "FAIL"
    )

    return {
        "checks":     checks,
        "details":    details,
        "pass_count": pass_count,
        "total":      len(checks),
        "verdict":    verdict,
    }


def print_scorecard(scorecard: dict[str, Any]) -> None:
    checks  = scorecard["checks"]
    details = scorecard["details"]
    verdict = scorecard["verdict"]
    passed  = scorecard["pass_count"]
    total   = scorecard["total"]

    width = 66
    print("=" * width)
    print("  PARAM CAPITAL — STRATEGY SCORECARD")
    print("=" * width)
    for key, passed_check in checks.items():
        icon  = "✓" if passed_check else "✗"
        label = details.get(key, key)
        print(f"  {icon}  {label}")
    print("-" * width)
    color = "\033[92m" if verdict == "PASS" else ("\033[93m" if verdict == "CONDITIONAL" else "\033[91m")
    reset = "\033[0m"
    print(f"  {color}{verdict}{reset}  ({passed}/{total} criteria passed)")
    print("=" * width)


def to_dataframe(scorecard: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key, passed in scorecard["checks"].items():
        rows.append({
            "Criterion": key,
            "Passed":    passed,
            "Detail":    scorecard["details"].get(key, ""),
        })
    df = pd.DataFrame(rows)
    df.loc[len(df)] = {"Criterion": "VERDICT", "Passed": scorecard["verdict"] == "PASS",
                       "Detail": scorecard["verdict"]}
    return df


# ── helpers ────────────────────────────────────────────────────────────────────

def _safe(d: dict, dotpath: str, default: float) -> float:
    """Drill into nested dict via dot-separated path; return default if missing."""
    keys = dotpath.split(".")
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return float(cur) if cur is not None else default
