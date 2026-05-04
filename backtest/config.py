"""
PARAM Capital Backtesting Engine — Configuration
"""
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR    = BASE_DIR / "logs"

REPORTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ── Instrument specs ───────────────────────────────────────────────────────
INSTRUMENTS: dict = {
    "GOLDM": {
        "exchange":        "MCX",
        "lot_size":        10,          # grams per lot
        "tick_size":       1.0,         # ₹ per 10g
        "currency":        "INR",
        "spread_ticks":    1,
        "commission_pct":  0.0003,      # 0.03% round-turn
        "slippage_ticks":  1,
        "timeframe":       "5T",
        "data_file":       "GOLDM_5min.csv",
    },
    "SILVERMICM": {
        "exchange":        "MCX",
        "lot_size":        1_000,       # grams per lot
        "tick_size":       1.0,         # ₹ per kg
        "currency":        "INR",
        "spread_ticks":    2,
        "commission_pct":  0.0003,
        "slippage_ticks":  1,
        "timeframe":       "1T",
        "data_file":       "SILVERMICM_1min.csv",
    },
    "EURUSD": {
        "exchange":        "FX",
        "lot_size":        1_000,       # micro lot units
        "tick_size":       0.00001,
        "currency":        "USD",
        "spread_ticks":    2,
        "commission_pct":  0.00002,
        "slippage_ticks":  1,
        "timeframe":       "15T",
        "data_file":       "EURUSD_M15.csv",
    },
}

# ── Portfolio defaults ─────────────────────────────────────────────────────
INIT_CASH: float = 1_000_000  # INR / USD depending on instrument

# ── Walk-forward ───────────────────────────────────────────────────────────
WFA_N_SPLITS:   int   = 8
WFA_TRAIN_FRAC: float = 0.70

# ── Monte Carlo ────────────────────────────────────────────────────────────
MC_N_SIMS:     int   = 3_000
MC_RUIN_LEVEL: float = 0.50   # 50% equity drawdown = ruin event

# ── Parameter sensitivity grids ────────────────────────────────────────────
SENSITIVITY_SL_MULTS: list = [0.8, 1.0, 1.2, 1.5, 2.0]
SENSITIVITY_TP_MULTS: list = [1.5, 2.0, 2.3, 3.0, 4.0]

# ── PARAM Capital scorecard thresholds ────────────────────────────────────
SCORECARD_THRESHOLDS: dict = {
    "oos_is_sharpe_ratio":       0.50,   # OOS/IS Sharpe ≥ 0.5
    "min_trades":                200,    # total trades ≥ 200
    "min_profit_factor":         1.30,   # PF ≥ 1.3
    "min_calmar":                0.50,   # Calmar ≥ 0.5
    "min_wfa_profitable_windows":  6,    # of 8 windows profitable
    "max_param_sensitivity":     0.30,   # σ/μ of Sharpe < 0.30
    "max_mc_ruin_pct":           5.0,    # ruin probability < 5%
    "min_t_stat":                2.0,    # |t-stat| > 2.0
    "min_profitable_regimes":    3,      # profitable in ≥ 3 regimes
    "min_2x_slip_sharpe":        1.0,    # Sharpe at 2× slippage ≥ 1.0
}
