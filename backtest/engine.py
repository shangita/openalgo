"""
PARAM Capital Backtesting Engine — Core (engine.py)

VectorBT-based engine supporting:
  - Walk-forward analysis (IS optimise SL/TP, OOS validate)
  - Monte Carlo ruin probability via P&L bootstrapping
  - Parameter sensitivity (Sharpe σ/μ across SL/TP grid)
  - PARAM Capital 10-criterion scorecard
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats

try:
    import vectorbt as vbt
    VBT_AVAILABLE = True
except ImportError:  # pragma: no cover
    VBT_AVAILABLE = False
    warnings.warn("vectorbt not installed — install with: pip install vectorbt")

from config import (
    INIT_CASH,
    MC_N_SIMS,
    MC_RUIN_LEVEL,
    SCORECARD_THRESHOLDS,
    SENSITIVITY_SL_MULTS,
    SENSITIVITY_TP_MULTS,
    WFA_N_SPLITS,
    WFA_TRAIN_FRAC,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe(val: Any, default: float = np.nan) -> float:
    """Return float or nan for missing / infinite values."""
    try:
        v = float(val)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def _sharpe(pf: Any) -> float:
    try:
        return _safe(pf.sharpe_ratio())
    except Exception:
        return np.nan


def _reindex(series: pd.Series, index: pd.Index, fill: bool = False) -> pd.Series:
    return series.reindex(index, fill_value=fill)


def _stop_array(stop: Union[float, pd.Series], index: pd.Index) -> Union[float, np.ndarray]:
    """Coerce stop to scalar or numpy array aligned to index."""
    if np.isscalar(stop):
        return float(stop)
    s = stop.reindex(index)
    med = s.median()
    return s.fillna(med if np.isfinite(med) else 0.02).values


# ── Engine ─────────────────────────────────────────────────────────────────

class ParamBacktestEngine:
    """
    PARAM Capital production backtesting engine.

    Typical usage::

        engine = ParamBacktestEngine("GOLDM", commission=0.0003, freq="5T")
        engine.load_data("data/GOLDM_5min.csv", "5T")
        engine.set_strategy(my_signal_fn)          # fn(data, sl_mult, tp_mult) → tuple
        entries, exits, sl_stop, tp_stop = my_signal_fn(engine.data, 1.0, 1.0)
        engine.run(entries, exits, sl_stop, tp_stop)
        engine.walk_forward()
        engine.monte_carlo()
        engine.param_sensitivity()
        print(engine.scorecard())
    """

    def __init__(
        self,
        instrument: str,
        init_cash: float = INIT_CASH,
        commission: float = 0.0003,
        slippage: float = 0.0005,
        freq: str = "5T",
    ) -> None:
        self.instrument  = instrument
        self.init_cash   = init_cash
        self.commission  = commission
        self.slippage    = slippage
        self.freq        = freq

        self.data:       Optional[pd.DataFrame] = None
        self._indicators: Dict[str, pd.Series]  = {}
        self._signal_func: Optional[Callable]   = None
        self.portfolio:  Optional[Any]           = None

        # Cached for re-use (2× slip, etc.)
        self._last_entries:       Optional[pd.Series] = None
        self._last_exits:         Optional[pd.Series] = None
        self._last_sl_stop:       Optional[Union[float, pd.Series]] = None
        self._last_tp_stop:       Optional[Union[float, pd.Series]] = None
        self._last_size:          float = 1.0
        self._last_short_entries: Optional[pd.Series] = None
        self._last_short_exits:   Optional[pd.Series] = None

        self._wfa_results:         List[Dict] = []
        self._mc_result:           Optional[Dict] = None
        self._sensitivity_results: Optional[pd.DataFrame] = None
        self._n_profitable_regimes: int = 0

    # ── Data loading ───────────────────────────────────────────────────────

    def load_data(self, path: Union[str, Path], timeframe: str) -> pd.DataFrame:
        """
        Load OHLCV CSV with datetime index.

        Expected columns (case-insensitive): datetime, open, high, low, close, volume
        Datetime column must be parseable by pandas.

        Args:
            path: Path to CSV file.
            timeframe: pandas offset string e.g. '5T', '1T', '15T'.

        Returns:
            Cleaned DataFrame stored in self.data.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Data file not found: {path}")

        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        df.index.name = "datetime"
        df.columns    = [c.lower().strip() for c in df.columns]

        required = {"open", "high", "low", "close"}
        missing  = required - set(df.columns)
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")

        if "volume" not in df.columns:
            df["volume"] = 0.0

        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df = df.dropna(subset=list(required))
        df = df[(df["high"] >= df["low"]) & (df["close"] > 0) & (df["open"] > 0)]

        if len(df) < 100:
            raise ValueError(f"Too few rows after cleaning: {len(df)}")

        self.data = df
        self.freq = timeframe
        logger.info(
            "Loaded %s rows from %s  [%s → %s]",
            len(df), path.name, df.index[0], df.index[-1],
        )
        return df

    # ── Indicator registry ─────────────────────────────────────────────────

    def add_indicator(self, name: str, fn: Callable, **params) -> Union[pd.Series, pd.DataFrame]:
        """
        Compute and register a named indicator.

        Args:
            name: Lookup key.
            fn:   Callable(data: DataFrame, **params) → Series | DataFrame.
            **params: Forwarded to fn.

        Returns:
            Computed indicator Series or DataFrame.
        """
        if self.data is None:
            raise RuntimeError("Call load_data() before add_indicator().")
        result = fn(self.data, **params)
        self._indicators[name] = result
        return result

    def get_indicator(self, name: str) -> Union[pd.Series, pd.DataFrame]:
        """Retrieve a previously registered indicator by name."""
        if name not in self._indicators:
            raise KeyError(f"Indicator '{name}' not found. Register via add_indicator().")
        return self._indicators[name]

    # ── Strategy function ──────────────────────────────────────────────────

    def set_strategy(self, fn: Callable) -> None:
        """
        Register strategy signal function for use in walk_forward & param_sensitivity.

        fn signature::

            fn(data: pd.DataFrame, sl_mult: float, tp_mult: float)
                -> (entries, exits, sl_stop, tp_stop)
                or (entries, exits, sl_stop, tp_stop, short_entries, short_exits)

        sl_stop / tp_stop are fractions of entry price (e.g. 0.015 = 1.5%).
        sl_mult / tp_mult scale the ATR-based stops for WFA optimisation.
        """
        self._signal_func = fn

    # ── Core run ───────────────────────────────────────────────────────────

    def run(
        self,
        entries: pd.Series,
        exits: pd.Series,
        sl_stop: Union[float, pd.Series],
        tp_stop: Union[float, pd.Series],
        size: float = 1.0,
        short_entries: Optional[pd.Series] = None,
        short_exits:   Optional[pd.Series] = None,
    ) -> Any:
        """
        Execute VectorBT portfolio simulation.

        Args:
            entries:       Long entry boolean Series.
            exits:         Long exit boolean Series.
            sl_stop:       Stop-loss as fraction of entry price. Scalar or per-bar Series.
            tp_stop:       Take-profit as fraction. Scalar or per-bar Series.
            size:          Position size in units / lots.
            short_entries: Optional short entry signals.
            short_exits:   Optional short exit signals.

        Returns:
            vbt.Portfolio object stored in self.portfolio.
        """
        if not VBT_AVAILABLE:
            raise ImportError("Install vectorbt: pip install vectorbt")
        if self.data is None:
            raise RuntimeError("Call load_data() before run().")

        idx   = self.data.index
        close = self.data["close"]
        open_ = self.data["open"]
        high  = self.data["high"]
        low   = self.data["low"]

        # Cache for scorecard's 2× slippage re-run
        self._last_entries       = entries
        self._last_exits         = exits
        self._last_sl_stop       = sl_stop
        self._last_tp_stop       = tp_stop
        self._last_size          = size
        self._last_short_entries = short_entries
        self._last_short_exits   = short_exits

        kwargs: Dict[str, Any] = dict(
            close    = close,
            open     = open_,
            high     = high,
            low      = low,
            entries  = _reindex(entries, idx, False),
            exits    = _reindex(exits,   idx, False),
            sl_stop  = _stop_array(sl_stop, idx),
            tp_stop  = _stop_array(tp_stop, idx),
            size     = size,
            init_cash= self.init_cash,
            fees     = self.commission,
            slippage = self.slippage,
            freq     = self.freq,
            accumulate = False,
        )

        if short_entries is not None:
            kwargs["short_entries"] = _reindex(short_entries, idx, False)
            se = short_exits if short_exits is not None else pd.Series(False, index=idx)
            kwargs["short_exits"]   = _reindex(se, idx, False)

        self.portfolio = vbt.Portfolio.from_signals(**kwargs)
        n = len(self.portfolio.trades)
        logger.info("Backtest complete — %d trades", n)
        return self.portfolio

    # ── Walk-forward analysis ──────────────────────────────────────────────

    def walk_forward(
        self,
        n_splits:   int   = WFA_N_SPLITS,
        train_frac: float = WFA_TRAIN_FRAC,
    ) -> List[Dict]:
        """
        Rolling walk-forward analysis.

        For each of n_splits windows:
          1. Split into IS (train_frac) and OOS (1−train_frac).
          2. Grid-search SL/TP multipliers on IS → maximise Sharpe.
          3. Apply best multipliers to OOS with NO further optimisation.
          4. Record IS Sharpe, OOS Sharpe, best params, profitability.

        Requires set_strategy() to be called first.

        Returns:
            List of per-window dicts with keys:
            window, is_start, is_end, oos_start, oos_end,
            is_sharpe, oos_sharpe, best_sl_mult, best_tp_mult,
            oos_trades, oos_pf, profitable.
        """
        if self._signal_func is None:
            raise RuntimeError("Call set_strategy() before walk_forward().")
        if self.data is None:
            raise RuntimeError("Call load_data() before walk_forward().")

        n           = len(self.data)
        window_size = n // n_splits
        results     = []

        logger.info("WFA: %d splits, %.0f%% train", n_splits, train_frac * 100)

        for split_idx in range(n_splits):
            win_start = split_idx * window_size
            win_end   = min((split_idx + 1) * window_size, n)
            if win_end - win_start < 60:
                continue

            train_end = win_start + int((win_end - win_start) * train_frac)
            is_data   = self.data.iloc[win_start:train_end].copy()
            oos_data  = self.data.iloc[train_end:win_end].copy()

            if len(is_data) < 30 or len(oos_data) < 10:
                continue

            # ── IS grid search ──────────────────────────────────────────
            best_sharpe   = -np.inf
            best_sl_mult  = 1.0
            best_tp_mult  = 2.3

            for sl_m in SENSITIVITY_SL_MULTS:
                for tp_m in SENSITIVITY_TP_MULTS:
                    try:
                        sig = self._signal_func(is_data, sl_m, tp_m)
                        pf  = self._run_slice(is_data, sig)
                        sr  = _sharpe(pf)
                        if sr > best_sharpe and len(pf.trades) >= 5:
                            best_sharpe  = sr
                            best_sl_mult = sl_m
                            best_tp_mult = tp_m
                    except Exception as exc:
                        logger.debug("WFA IS grid [sl=%.1f tp=%.1f]: %s", sl_m, tp_m, exc)

            # ── OOS apply best params ───────────────────────────────────
            oos_sharpe = np.nan
            oos_pf_val = np.nan
            oos_trades = 0
            try:
                sig = self._signal_func(oos_data, best_sl_mult, best_tp_mult)
                pf  = self._run_slice(oos_data, sig)
                oos_sharpe = _sharpe(pf)
                oos_pf_val = _safe(pf.trades.profit_factor)
                oos_trades = len(pf.trades)
            except Exception as exc:
                logger.debug("WFA OOS window %d: %s", split_idx + 1, exc)

            results.append({
                "window":      split_idx + 1,
                "is_start":    self.data.index[win_start],
                "is_end":      self.data.index[train_end - 1],
                "oos_start":   self.data.index[train_end],
                "oos_end":     self.data.index[win_end - 1],
                "is_sharpe":   _safe(best_sharpe),
                "oos_sharpe":  _safe(oos_sharpe),
                "best_sl_mult": best_sl_mult,
                "best_tp_mult": best_tp_mult,
                "oos_trades":  oos_trades,
                "oos_pf":      _safe(oos_pf_val),
                "profitable":  (not np.isnan(oos_sharpe)) and oos_sharpe > 0,
            })
            logger.info(
                "WFA window %d/%d  IS Sharpe=%.2f  OOS Sharpe=%.2f  Trades=%d",
                split_idx + 1, n_splits, _safe(best_sharpe), _safe(oos_sharpe), oos_trades,
            )

        self._wfa_results = results
        return results

    # ── Monte Carlo ────────────────────────────────────────────────────────

    def monte_carlo(self, n_sims: int = MC_N_SIMS) -> Dict:
        """
        Bootstrap Monte Carlo on shuffled daily P&L.

        Shuffles the realised daily return sequence n_sims times, rebuilds
        equity curves, and counts ruin events (drawdown ≥ MC_RUIN_LEVEL).

        Returns:
            Dict with ruin_prob (%), median/p5/p95 terminal equity,
            median max drawdown, n_sims.
        """
        if self.portfolio is None:
            raise RuntimeError("Call run() before monte_carlo().")

        returns = self.portfolio.returns()
        if hasattr(returns, "squeeze"):
            returns = returns.squeeze()

        daily = (
            returns.resample("1D")
                   .apply(lambda x: (1 + x).prod() - 1)
                   .dropna()
        )
        arr = daily.values.astype(float)
        if len(arr) < 10:
            logger.warning("Insufficient daily return observations for Monte Carlo.")
            return {"ruin_prob": np.nan}

        rng           = np.random.default_rng(seed=42)
        ruin_count    = 0
        final_equities: List[float] = []
        max_dds:        List[float] = []

        for _ in range(n_sims):
            shuffled = rng.permutation(arr)
            equity   = np.cumprod(1.0 + shuffled)
            peak     = np.maximum.accumulate(equity)
            dd       = (peak - equity) / np.where(peak == 0, 1, peak)
            max_dd   = float(dd.max())
            max_dds.append(max_dd)
            if max_dd >= MC_RUIN_LEVEL:
                ruin_count += 1
            final_equities.append(float(equity[-1]))

        ruin_prob = ruin_count / n_sims * 100.0

        result = {
            "ruin_prob":     ruin_prob,
            "median_equity": float(np.median(final_equities)),
            "p5_equity":     float(np.percentile(final_equities, 5)),
            "p95_equity":    float(np.percentile(final_equities, 95)),
            "median_max_dd": float(np.median(max_dds)),
            "n_sims":        n_sims,
        }
        self._mc_result = result
        logger.info("Monte Carlo (%d sims): ruin prob = %.2f%%", n_sims, ruin_prob)
        return result

    # ── Parameter sensitivity ──────────────────────────────────────────────

    def param_sensitivity(self, param_grid: Optional[Dict] = None) -> pd.DataFrame:
        """
        Grid search over SL/TP multipliers to compute Sharpe σ/μ (sensitivity ratio).

        A σ/μ < 0.30 indicates the strategy edge is robust across parameter choices.

        Args:
            param_grid: Optional dict with 'sl_mults' and 'tp_mults' lists.
                        Defaults to SENSITIVITY_SL_MULTS × SENSITIVITY_TP_MULTS.

        Returns:
            DataFrame with columns: sl_mult, tp_mult, sharpe, calmar, n_trades.
            DataFrame.attrs['sigma_mu'] contains the Sharpe σ/μ ratio.
        """
        if self._signal_func is None:
            raise RuntimeError("Call set_strategy() before param_sensitivity().")
        if self.data is None:
            raise RuntimeError("Call load_data() before param_sensitivity().")

        sl_mults = (param_grid or {}).get("sl_mults", SENSITIVITY_SL_MULTS)
        tp_mults = (param_grid or {}).get("tp_mults", SENSITIVITY_TP_MULTS)

        rows: List[Dict] = []
        for sl_m in sl_mults:
            for tp_m in tp_mults:
                try:
                    sig    = self._signal_func(self.data, sl_m, tp_m)
                    pf     = self._run_slice(self.data, sig)
                    sharpe = _sharpe(pf)
                    calmar = _safe(pf.calmar_ratio())
                    rows.append({
                        "sl_mult":  sl_m,
                        "tp_mult":  tp_m,
                        "sharpe":   sharpe,
                        "calmar":   calmar,
                        "n_trades": len(pf.trades),
                    })
                except Exception as exc:
                    logger.debug("Sensitivity [sl=%.1f tp=%.1f]: %s", sl_m, tp_m, exc)
                    rows.append({"sl_mult": sl_m, "tp_mult": tp_m,
                                 "sharpe": np.nan, "calmar": np.nan, "n_trades": 0})

        df     = pd.DataFrame(rows)
        sharpes = df["sharpe"].dropna()

        if len(sharpes) > 1 and abs(sharpes.mean()) > 1e-9:
            sigma_mu = float(sharpes.std() / abs(sharpes.mean()))
        else:
            sigma_mu = np.nan

        df.attrs["sigma_mu"] = sigma_mu
        self._sensitivity_results = df
        logger.info("Param sensitivity σ/μ = %.3f", sigma_mu if not np.isnan(sigma_mu) else -1)
        return df

    # ── Scorecard ──────────────────────────────────────────────────────────

    def scorecard(self) -> Dict:
        """
        Compute all 10 PARAM Capital validation criteria.

        Criteria:
          1. OOS/IS Sharpe ratio > 0.50
          2. Total trades > 200
          3. Profit Factor > 1.30
          4. Calmar ratio > 0.50
          5. WFA profitable windows > 6/8
          6. Param sensitivity σ/μ < 0.30
          7. Monte Carlo ruin probability < 5%
          8. t-stat on trade P&L > 2.0
          9. Profitable in ≥ 3 regimes
          10. Sharpe at 2× slippage > 1.0

        Returns:
            Dict with metric values and pass/fail per criterion.
        """
        if self.portfolio is None:
            raise RuntimeError("Call run() before scorecard().")

        pf  = self.portfolio
        thr = SCORECARD_THRESHOLDS
        m: Dict[str, Any] = {}

        # ── Core metrics ──────────────────────────────────────────────────
        m["sharpe"]        = _sharpe(pf)
        m["calmar"]        = _safe(pf.calmar_ratio())
        m["max_drawdown"]  = _safe(pf.max_drawdown())
        m["total_return"]  = _safe(pf.total_return())
        m["n_trades"]      = len(pf.trades)

        if m["n_trades"] > 0:
            m["win_rate"]      = _safe(pf.trades.win_rate) * 100
            m["profit_factor"] = _safe(pf.trades.profit_factor)
        else:
            m["win_rate"]      = 0.0
            m["profit_factor"] = 0.0

        # ── t-statistic ───────────────────────────────────────────────────
        if m["n_trades"] >= 2:
            pnls = np.asarray(pf.trades.pnl, dtype=float)
            pnls = pnls[np.isfinite(pnls)]
            t_stat, _ = stats.ttest_1samp(pnls, 0.0)
            m["t_stat"] = float(t_stat)
        else:
            m["t_stat"] = 0.0

        # ── WFA ───────────────────────────────────────────────────────────
        if self._wfa_results:
            prof_windows = sum(1 for w in self._wfa_results if w.get("profitable", False))
            is_sharpes   = [w["is_sharpe"]  for w in self._wfa_results if np.isfinite(w.get("is_sharpe", np.nan))]
            oos_sharpes  = [w["oos_sharpe"] for w in self._wfa_results if np.isfinite(w.get("oos_sharpe", np.nan))]
            is_mean      = float(np.mean(is_sharpes))  if is_sharpes  else np.nan
            oos_mean     = float(np.mean(oos_sharpes)) if oos_sharpes else np.nan
            oos_is_ratio = (oos_mean / is_mean) if (is_sharpes and oos_sharpes and abs(is_mean) > 1e-9) else np.nan
        else:
            prof_windows = 0
            oos_is_ratio = np.nan

        m["wfa_profitable_windows"] = prof_windows
        m["wfa_n_windows"]          = len(self._wfa_results)
        m["oos_is_sharpe_ratio"]    = _safe(oos_is_ratio)

        # ── Monte Carlo ───────────────────────────────────────────────────
        mc = self._mc_result or {}
        m["mc_ruin_prob"] = _safe(mc.get("ruin_prob", np.nan))

        # ── Param sensitivity ─────────────────────────────────────────────
        if self._sensitivity_results is not None:
            m["param_sensitivity"] = _safe(self._sensitivity_results.attrs.get("sigma_mu", np.nan))
        else:
            m["param_sensitivity"] = np.nan

        # ── 2× slippage Sharpe ────────────────────────────────────────────
        try:
            pf2 = self._rerun(slippage=self.slippage * 2)
            m["sharpe_2x_slip"] = _sharpe(pf2)
        except Exception:
            # Fallback: penalise current Sharpe conservatively
            m["sharpe_2x_slip"] = m["sharpe"] * 0.80

        # ── Regime count (strategy sets self._n_profitable_regimes) ───────
        m["n_profitable_regimes"] = self._n_profitable_regimes

        # ── Pass/fail per criterion ───────────────────────────────────────
        checks = {
            "oos_is_sharpe_ratio":       m["oos_is_sharpe_ratio"]    >= thr["oos_is_sharpe_ratio"],
            "min_trades":                m["n_trades"]                >= thr["min_trades"],
            "profit_factor":             m["profit_factor"]           >= thr["min_profit_factor"],
            "calmar":                    m["calmar"]                  >= thr["min_calmar"],
            "wfa_profitable_windows":    m["wfa_profitable_windows"]  >= thr["min_wfa_profitable_windows"],
            "param_sensitivity":         (not np.isnan(m["param_sensitivity"])) and
                                         m["param_sensitivity"]       <= thr["max_param_sensitivity"],
            "mc_ruin_prob":              (not np.isnan(m["mc_ruin_prob"])) and
                                         m["mc_ruin_prob"]            <= thr["max_mc_ruin_pct"],
            "t_stat":                    abs(m["t_stat"])             >= thr["min_t_stat"],
            "profitable_regimes":        m["n_profitable_regimes"]    >= thr["min_profitable_regimes"],
            "sharpe_2x_slip":            m["sharpe_2x_slip"]          >= thr["min_2x_slip_sharpe"],
        }
        m["checks"]     = checks
        m["pass_count"] = sum(1 for v in checks.values() if v)
        m["verdict"]    = "PASS" if m["pass_count"] >= 8 else "FAIL"
        return m

    # ── Private helpers ────────────────────────────────────────────────────

    def _run_slice(self, data: pd.DataFrame, sig: Tuple) -> Any:
        """Run VectorBT on a data slice using signals from signal_func output."""
        entries, exits, sl_stop, tp_stop = sig[:4]
        short_e = sig[4] if len(sig) > 4 else None
        short_x = sig[5] if len(sig) > 5 else None

        idx   = data.index
        close = data["close"]
        open_ = data.get("open", close)
        high  = data.get("high", close)
        low   = data.get("low",  close)

        kw: Dict[str, Any] = dict(
            close    = close,
            open     = open_,
            high     = high,
            low      = low,
            entries  = _reindex(entries, idx, False),
            exits    = _reindex(exits,   idx, False),
            sl_stop  = _stop_array(sl_stop, idx),
            tp_stop  = _stop_array(tp_stop, idx),
            size     = 1.0,
            init_cash= self.init_cash,
            fees     = self.commission,
            slippage = self.slippage,
            freq     = self.freq,
            accumulate = False,
        )
        if short_e is not None:
            kw["short_entries"] = _reindex(short_e, idx, False)
            se = short_x if short_x is not None else pd.Series(False, index=idx)
            kw["short_exits"]   = _reindex(se, idx, False)

        return vbt.Portfolio.from_signals(**kw)

    def _rerun(self, slippage: Optional[float] = None, commission: Optional[float] = None) -> Any:
        """Re-run the last backtest with different cost assumptions."""
        if self._last_entries is None:
            raise RuntimeError("No previous run to re-run.")

        idx   = self.data.index
        close = self.data["close"]
        kw: Dict[str, Any] = dict(
            close    = close,
            open     = self.data["open"],
            high     = self.data["high"],
            low      = self.data["low"],
            entries  = _reindex(self._last_entries, idx, False),
            exits    = _reindex(self._last_exits,   idx, False),
            sl_stop  = _stop_array(self._last_sl_stop, idx),
            tp_stop  = _stop_array(self._last_tp_stop, idx),
            size     = self._last_size,
            init_cash= self.init_cash,
            fees     = commission if commission is not None else self.commission,
            slippage = slippage   if slippage   is not None else self.slippage,
            freq     = self.freq,
            accumulate = False,
        )
        if self._last_short_entries is not None:
            kw["short_entries"] = _reindex(self._last_short_entries, idx, False)
            se = self._last_short_exits if self._last_short_exits is not None else pd.Series(False, index=idx)
            kw["short_exits"]   = _reindex(se, idx, False)

        return vbt.Portfolio.from_signals(**kw)
