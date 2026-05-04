"""
PARAM Capital Backtesting Engine — Run All Strategies
------------------------------------------------------
Usage:
    python run_all.py                          # run all registered strategies
    python run_all.py --strategy "GOLDM EMA"  # run matching strategy
    python run_all.py --no-wfa --no-mc        # quick run: skip slow analyses
    python run_all.py --list                   # print available strategies
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from config import REPORTS_DIR, LOGS_DIR
from engine import ParamBacktestEngine
import tearsheet
import scorecard as sc
from strategies import REGISTRY


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PARAM Capital — Run backtest(s)")
    p.add_argument("--strategy",  type=str, default=None, help="Partial strategy name match")
    p.add_argument("--no-wfa",    action="store_true",    help="Skip walk-forward analysis")
    p.add_argument("--no-mc",     action="store_true",    help="Skip Monte Carlo")
    p.add_argument("--no-sens",   action="store_true",    help="Skip param sensitivity")
    p.add_argument("--list",      action="store_true",    help="List strategies and exit")
    p.add_argument("--sl",        type=float, default=1.0, help="Base SL multiplier")
    p.add_argument("--tp",        type=float, default=2.3, help="Base TP multiplier")
    return p.parse_args()


def run_strategy(meta: dict, args: argparse.Namespace) -> dict:
    name       = meta["name"]
    instrument = meta["instrument"]
    signal_fn  = meta["signal_fn"]

    print(f"\n{'='*66}")
    print(f"  {name}  [{instrument}]")
    print(f"{'='*66}")

    eng = ParamBacktestEngine(instrument)
    eng.load_data()
    eng.set_strategy(signal_fn)

    t0 = time.time()
    stats = eng.run(sl_mult=args.sl, tp_mult=args.tp)
    print(f"  IS run complete — {time.time()-t0:.1f}s")
    _print_stats(stats)

    results: dict = {"stats": stats}

    # Walk-forward
    if not args.no_wfa:
        print("  Running walk-forward analysis …")
        t0 = time.time()
        wfa = eng.walk_forward()
        print(f"  WFA done — {time.time()-t0:.1f}s  |  "
              f"IS Sharpe={wfa['is_sharpe_mean']:.3f}  OOS Sharpe={wfa['oos_sharpe_mean']:.3f}  "
              f"profitable={wfa['profitable_windows']}/8")
        results["wfa_results"] = wfa
    else:
        results["wfa_results"] = {"is_sharpe_mean": 0, "oos_sharpe_mean": 0, "profitable_windows": 0}

    # Monte Carlo
    if not args.no_mc:
        print("  Running Monte Carlo (3 000 sims) …")
        t0 = time.time()
        mc = eng.monte_carlo()
        print(f"  MC done — {time.time()-t0:.1f}s  |  ruin={mc['ruin_pct']:.2f}%  "
              f"median_final={mc['median_final_equity']:.0f}")
        results["mc_results"] = mc
    else:
        results["mc_results"] = {"ruin_pct": 100.0, "median_final_equity": 0}

    # Param sensitivity
    if not args.no_sens:
        print("  Running parameter sensitivity grid …")
        t0 = time.time()
        sens = eng.param_sensitivity()
        print(f"  Sensitivity done — {time.time()-t0:.1f}s  |  Sharpe CV={sens['sharpe_cv']:.3f}")
        results["sensitivity_results"] = sens
    else:
        results["sensitivity_results"] = {"sharpe_cv": 1.0}

    # Regime analysis (stub — engine exposes last_returns for custom regime slicing)
    results["regime_results"] = _regime_analysis(eng)

    # 2× slippage re-run
    print("  Re-running at 2× slippage …")
    slip2x = eng._rerun(slippage=eng.slippage * 2, commission=eng.commission)
    results["slip2x_stats"] = slip2x
    print(f"  2× slip Sharpe = {slip2x.get('sharpe', 0):.3f}")

    # Scorecard
    card = sc.evaluate(results)
    sc.print_scorecard(card)
    results["scorecard"] = card

    # Tearsheet
    if eng.last_returns is not None and len(eng.last_returns) > 0:
        ts_path = tearsheet.generate(eng.last_returns, name)
        print(f"  Tearsheet → {ts_path}")
        monthly_path = tearsheet.monthly_heatmap(eng.last_returns, name)
        print(f"  Heatmap   → {monthly_path}")

    # Excel summary
    _save_excel(name, stats, card)

    return results


def _regime_analysis(eng: ParamBacktestEngine) -> dict:
    """Slice IS returns into 4 market regimes and count profitable ones."""
    if eng.last_returns is None or len(eng.last_returns) < 100:
        return {"profitable_regimes": 0}

    ret = eng.last_returns.dropna()
    n   = len(ret)
    q   = n // 4
    regimes = [ret.iloc[i * q: (i + 1) * q] for i in range(4)]
    profitable = sum(1 for r in regimes if r.sum() > 0)
    return {"profitable_regimes": profitable}


def _print_stats(stats: dict) -> None:
    fields = ["total_trades", "sharpe", "calmar", "profit_factor", "max_drawdown_pct"]
    parts  = []
    for f in fields:
        v = stats.get(f)
        if v is not None:
            parts.append(f"{f}={v:.3f}" if isinstance(v, float) else f"{f}={v}")
    print("  " + "  |  ".join(parts))


def _save_excel(strategy_name: str, stats: dict, card: dict) -> None:
    slug  = strategy_name.replace(" ", "_").replace("/", "-")
    fname = REPORTS_DIR / f"{slug}_scorecard.xlsx"
    with pd.ExcelWriter(str(fname), engine="openpyxl") as xl:
        pd.DataFrame([stats]).T.rename(columns={0: "Value"}).to_excel(xl, sheet_name="Stats")
        sc.to_dataframe(card).to_excel(xl, sheet_name="Scorecard", index=False)
    print(f"  Excel     → {fname}")


def main() -> None:
    args = parse_args()

    if args.list:
        print("\nAvailable strategies:")
        for name, meta in REGISTRY.items():
            print(f"  • {name}  [{meta['instrument']}]  — {meta['description']}")
        return

    targets = {
        name: meta
        for name, meta in REGISTRY.items()
        if args.strategy is None or args.strategy.lower() in name.lower()
    }

    if not targets:
        print(f"No strategy matches '{args.strategy}'. Use --list to see options.")
        sys.exit(1)

    all_results = {}
    for name, meta in targets.items():
        try:
            all_results[name] = run_strategy(meta, args)
        except Exception as exc:
            print(f"\n  ERROR in {name}: {exc}")
            import traceback
            traceback.print_exc()

    # Comparison tearsheet when multiple strategies ran
    if len(all_results) > 1:
        returns_dict = {}
        for name, res in all_results.items():
            eng_ref = res.get("_engine")
            if eng_ref and eng_ref.last_returns is not None:
                returns_dict[name] = eng_ref.last_returns

    print(f"\nAll done. Reports in {REPORTS_DIR}")


if __name__ == "__main__":
    main()
