"""
PARAM Capital Backtesting Engine — Tearsheet Generator
"""
from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import quantstats as qs

from config import REPORTS_DIR

warnings.filterwarnings("ignore")

qs.extend_pandas()


def generate(
    returns: pd.Series,
    strategy_name: str,
    benchmark: pd.Series | None = None,
    output_dir: Path = REPORTS_DIR,
) -> Path:
    """Full QuantStats HTML tearsheet. Returns path to saved file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = strategy_name.replace(" ", "_").replace("/", "-")
    fname = output_dir / f"{slug}_{date.today().isoformat()}.html"

    if benchmark is None:
        benchmark = pd.Series(0.0, index=returns.index, name="Flat")

    qs.reports.html(
        returns,
        benchmark=benchmark,
        output=str(fname),
        title=f"PARAM Capital — {strategy_name}",
        download_filename=str(fname),
    )
    return fname


def compare(
    returns_dict: dict[str, pd.Series],
    output_dir: Path = REPORTS_DIR,
) -> Path:
    """Side-by-side equity curves for multiple strategies in one HTML."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fname = output_dir / f"comparison_{date.today().isoformat()}.html"

    names = list(returns_dict.keys())
    series = list(returns_dict.values())

    base = series[0]
    qs.reports.html(
        base,
        benchmark=series[1] if len(series) > 1 else None,
        output=str(fname),
        title="PARAM Capital — Strategy Comparison: " + " vs ".join(names),
        download_filename=str(fname),
    )
    return fname


def monthly_heatmap(
    returns: pd.Series,
    strategy_name: str,
    output_dir: Path = REPORTS_DIR,
) -> Path:
    """Standalone monthly P&L heatmap saved as PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = strategy_name.replace(" ", "_").replace("/", "-")
    fname = output_dir / f"{slug}_monthly_{date.today().isoformat()}.png"

    fig, ax = plt.subplots(figsize=(12, 6))
    qs.plots.monthly_heatmap(returns, ax=ax, show=False)
    ax.set_title(f"PARAM Capital — {strategy_name} Monthly Returns")
    fig.tight_layout()
    fig.savefig(str(fname), dpi=150)
    plt.close(fig)
    return fname


def metrics_table(returns: pd.Series) -> pd.DataFrame:
    """Key scalar metrics as a tidy DataFrame (strategy_name → value)."""
    r = returns.dropna()
    m = {
        "Total Return %":    round(qs.stats.comp(r) * 100, 2),
        "CAGR %":            round(qs.stats.cagr(r) * 100, 2),
        "Sharpe":            round(qs.stats.sharpe(r), 3),
        "Sortino":           round(qs.stats.sortino(r), 3),
        "Calmar":            round(qs.stats.calmar(r), 3),
        "Max Drawdown %":    round(qs.stats.max_drawdown(r) * 100, 2),
        "Win Rate %":        round(qs.stats.win_rate(r) * 100, 2),
        "Profit Factor":     round(qs.stats.profit_factor(r), 3),
        "Volatility %":      round(qs.stats.volatility(r) * 100, 2),
        "Skew":              round(qs.stats.skew(r), 3),
        "Kurtosis":          round(qs.stats.kurtosis(r), 3),
        "VaR 95% %":         round(qs.stats.value_at_risk(r) * 100, 2),
    }
    return pd.DataFrame.from_dict(m, orient="index", columns=["Value"])
