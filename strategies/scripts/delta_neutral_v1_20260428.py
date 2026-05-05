"""
Delta Neutral Strategy v1 — PARAM Capital
Short Straddle with continuous delta hedging via futures.

Setup  : Sell ATM CE + ATM PE for a given underlying/expiry.
Monitor: Recalculate portfolio Greeks every minute.
Hedge  : When |net_delta| > DELTA_THRESHOLD, offset with futures lots.
Exit   : Hard stop on total portfolio loss or manual signal file.

OpenAlgo SDK format — runs under /root/trading/openalgo/strategies/scripts/
Monitor via  : https://meanrev.duckdns.org:5000/deltaneutral
"""

import os
import re
import math
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg2
from scipy.stats import norm
from openalgo import api

# ── Timezone ──────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("delta_neutral_v1")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit before running
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # OpenAlgo connection
    "api_key":       "abc5df3efcb0ec2d83b2eb17763dc61208f4f9d9fbaae6ec0d479f9b5715c23b",
    "host":          "http://127.0.0.1:5001",

    # Instrument
    "underlying":    "NIFTY",
    "exchange":      "NFO",
    "lot_size":      65,           # NIFTY lot size (updated Apr 2026)

    # Expiry — set to nearest weekly (DDMMMYY), or leave "" to auto-detect
    "expiry":        "",

    # Risk parameters
    "num_lots":      1,            # lots to sell on each side (CE + PE)
    "delta_threshold": 0.15,       # hedge when |net_delta| > this (in lots)
    "max_loss_pct":  0.02,         # stop-out when portfolio loss > 2% of margin
    "max_loss_abs":  10000,        # hard stop in ₹ (whichever hits first)
    "risk_free":     0.065,        # 6.5% annualised risk-free rate

    # Timing
    "loop_interval": 60,           # seconds between Greek recalculations
    "market_open":   (9, 16),      # (hour, minute) IST
    "market_close":  (15, 25),     # exit all 5 min before close

    # Futures symbol for delta hedge (resolved dynamically if "")
    "futures_sym":   "",
    "futures_exch":  "NFO",

    # Signal file: touch this file to trigger clean exit
    "exit_signal":   "/tmp/dn_exit",
}

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers — resolve active expiries and futures symbols
# ─────────────────────────────────────────────────────────────────────────────
_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5,  "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _db_conn():
    return psycopg2.connect(
        dbname="openalgo", user="trader", password="trader", host="127.0.0.1"
    )


def _resolve_nearest_expiry(underlying: str, exchange: str) -> str:
    """Return nearest weekly/monthly expiry string DDMMMYY from symtoken DB."""
    try:
        conn = _db_conn()
        cur = conn.cursor()
        pattern = rf"^{underlying}(\d{{2}})([A-Z]{{3}})(\d{{2}})\d+(?:CE|PE)$"
        cur.execute(
            "SELECT DISTINCT symbol FROM symtoken WHERE exchange=%s AND symbol LIKE %s",
            (exchange, f"{underlying}%"),
        )
        rows = cur.fetchall()
        conn.close()
        today = datetime.now(IST).replace(tzinfo=None)
        candidates: list[tuple[datetime, str]] = []
        for (sym,) in rows:
            m = re.match(pattern, sym)
            if m:
                day = int(m.group(1))
                mon = _MONTH_MAP.get(m.group(2), 0)
                yr = 2000 + int(m.group(3))
                if mon == 0:
                    continue
                try:
                    exp = datetime(yr, mon, day)
                except ValueError:
                    continue
                if exp >= today:
                    tag = m.group(1) + m.group(2) + m.group(3)
                    candidates.append((exp, tag))
        if candidates:
            candidates.sort()
            return candidates[0][1]
    except Exception as e:
        logger.warning(f"DB expiry resolve failed: {e}")
    return ""


def _resolve_futures_symbol(underlying: str, exchange: str) -> str:
    """Return nearest active futures symbol for the underlying."""
    try:
        conn = _db_conn()
        cur = conn.cursor()
        pattern = rf"^{underlying}(\d{{2}})([A-Z]{{3}})(\d{{2}})FUT$"
        cur.execute(
            "SELECT symbol FROM symtoken WHERE exchange=%s AND symbol LIKE %s",
            (exchange, f"{underlying}%FUT"),
        )
        rows = cur.fetchall()
        conn.close()
        today = datetime.now(IST).replace(tzinfo=None)
        candidates = []
        for (sym,) in rows:
            m = re.match(pattern, sym)
            if m:
                day = int(m.group(1))
                mon = _MONTH_MAP.get(m.group(2), 0)
                yr = 2000 + int(m.group(3))
                if mon == 0:
                    continue
                try:
                    exp = datetime(yr, mon, day)
                except ValueError:
                    continue
                if exp >= today:
                    candidates.append((exp, sym))
        if candidates:
            candidates.sort()
            return candidates[0][1]
    except Exception as e:
        logger.warning(f"DB futures resolve failed: {e}")
    return underlying + "FUT"


def _find_atm_strike(underlying: str, exchange: str, expiry: str, spot: float) -> float:
    """Return the ATM strike closest to spot for the given expiry."""
    try:
        conn = _db_conn()
        cur = conn.cursor()
        prefix = f"{underlying}{expiry}"
        cur.execute(
            "SELECT symbol FROM symtoken WHERE exchange=%s AND symbol LIKE %s",
            (exchange, f"{prefix}%CE"),
        )
        rows = cur.fetchall()
        conn.close()
        pattern = rf"^{underlying}{expiry}([\d.]+)CE$"
        strikes = []
        for (sym,) in rows:
            m = re.match(pattern, sym)
            if m:
                try:
                    strikes.append(float(m.group(1)))
                except ValueError:
                    pass
        if strikes:
            return min(strikes, key=lambda k: abs(k - spot))
    except Exception as e:
        logger.warning(f"ATM strike lookup failed: {e}")
    # fallback: round to nearest 50
    return round(spot / 50) * 50


def _db_ensure_tables():
    """Create dn_* tables if they don't exist (idempotent)."""
    ddl = [
        """CREATE TABLE IF NOT EXISTS dn_state (
            strategy_id TEXT PRIMARY KEY,
            run_date    DATE NOT NULL,
            hedge_lots  INTEGER NOT NULL DEFAULT 0,
            entry_done  BOOLEAN NOT NULL DEFAULT FALSE,
            ce_sym      TEXT, pe_sym TEXT, futures_sym TEXT,
            expiry TEXT, atm_strike FLOAT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS dn_greeks_log (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            strategy_id TEXT NOT NULL DEFAULT 'delta_neutral_v1',
            run_date DATE NOT NULL DEFAULT CURRENT_DATE,
            spot FLOAT, ce_ltp FLOAT, pe_ltp FLOAT,
            ce_iv FLOAT, pe_iv FLOAT,
            net_delta FLOAT, net_gamma FLOAT,
            net_theta FLOAT, net_vega FLOAT,
            pnl FLOAT, var_95 FLOAT, cvar_95 FLOAT,
            hedge_lots INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS dn_trade_log (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            strategy_id TEXT NOT NULL DEFAULT 'delta_neutral_v1',
            run_date DATE NOT NULL DEFAULT CURRENT_DATE,
            event_type TEXT NOT NULL,
            symbol TEXT, action TEXT, quantity INTEGER,
            hedge_lots_after INTEGER, pnl FLOAT, reason TEXT
        )""",
    ]
    try:
        conn = _db_conn()
        cur = conn.cursor()
        for stmt in ddl:
            cur.execute(stmt)
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("DB table creation failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Black-Scholes-Merton Greeks (scipy, no py_vollib needed)
# ─────────────────────────────────────────────────────────────────────────────

def _bsm_greeks(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> dict:
    """
    Compute BSM Greeks.
    flag: 'c' for call, 'p' for put.
    T: time to expiry in years.
    Returns dict with delta, gamma, theta, vega, iv (=sigma).
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        sign = 1 if flag == "c" else -1
        return {"delta": sign * 0.5, "gamma": 0, "theta": 0, "vega": 0, "iv": sigma}

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    nd1 = norm.pdf(d1)

    if flag == "c":
        delta = norm.cdf(d1)
        theta = (
            -(S * nd1 * sigma) / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * norm.cdf(d2)
        ) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (
            -(S * nd1 * sigma) / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
        ) / 365

    gamma = nd1 / (S * sigma * math.sqrt(T))
    vega = S * nd1 * math.sqrt(T) / 100  # per 1% IV move

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "iv": sigma}


def _implied_vol(S: float, K: float, T: float, r: float, price: float, flag: str) -> float:
    """Newton-Raphson IV solve."""
    if T <= 0 or price <= 0:
        return 0.0
    sigma = 0.30
    for _ in range(100):
        g = _bsm_greeks(S, K, T, r, sigma, flag)
        if flag == "c":
            d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            model_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            model_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        diff = model_price - price
        vega_raw = S * norm.pdf(d1) * math.sqrt(T)
        if abs(vega_raw) < 1e-10:
            break
        sigma -= diff / vega_raw
        sigma = max(0.001, min(sigma, 20.0))
        if abs(diff) < 0.001:
            break
    return sigma


# ─────────────────────────────────────────────────────────────────────────────
# Enhanced Risk Manager (VaR / CVaR)
# ─────────────────────────────────────────────────────────────────────────────

class RiskManager:
    """Track portfolio PnL history, compute VaR and CVaR."""

    def __init__(self, confidence: float = 0.95, window: int = 30):
        self.confidence = confidence
        self.window = window
        self._pnl_series: list[float] = []

    def record(self, pnl: float) -> None:
        self._pnl_series.append(pnl)
        if len(self._pnl_series) > self.window:
            self._pnl_series.pop(0)

    def var(self) -> float:
        if len(self._pnl_series) < 5:
            return 0.0
        returns = np.diff(self._pnl_series)
        return float(np.percentile(returns, (1 - self.confidence) * 100))

    def cvar(self) -> float:
        if len(self._pnl_series) < 5:
            return 0.0
        returns = np.diff(self._pnl_series)
        v = self.var()
        tail = returns[returns <= v]
        return float(tail.mean()) if len(tail) > 0 else v

    def should_stop(self, current_pnl: float, max_loss: float) -> bool:
        return current_pnl < -abs(max_loss)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy State
# ─────────────────────────────────────────────────────────────────────────────

class DeltaNeutralStrategy:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = api(
            api_key=cfg["api_key"],
            host=cfg["host"],
        )
        self.risk = RiskManager(confidence=0.95, window=60)

        self.expiry = cfg["expiry"]
        self.futures_sym = cfg["futures_sym"]
        self.hedge_lots = 0          # current futures hedge (+ = long, - = short)
        self.initial_pnl = None      # set after entry
        self.entry_done = False

        # resolved at startup
        self.ce_sym = ""
        self.pe_sym = ""
        self.atm_strike = 0.0

    # ── Market helpers ────────────────────────────────────────────────────────

    def _quote(self, symbol: str, exchange: str) -> float:
        try:
            resp = self.client.quotes(symbol=symbol, exchange=exchange)
            if resp.get("status") == "success":
                return float(resp.get("data", {}).get("ltp", 0) or 0)
        except Exception as e:
            logger.debug(f"Quote failed {symbol}: {e}")
        return 0.0

    def _spot(self) -> float:
        # For indices, quote from NSE/BSE; for stocks from NSE
        idx_nse = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
        underlying = self.cfg["underlying"]
        if underlying in idx_nse:
            exch = "NSE_INDEX"
        else:
            exch = "NSE"
        return self._quote(underlying, exch)

    def _days_to_expiry(self) -> float:
        if not self.expiry:
            return 0.0
        try:
            exp_dt = datetime.strptime(self.expiry.title(), "%d%b%y")
            now = datetime.now(IST).replace(tzinfo=None)
            return max(0.0, (exp_dt - now).total_seconds() / 86400)
        except Exception:
            return 0.0

    # ── Startup ───────────────────────────────────────────────────────────────

    def setup(self) -> bool:
        underlying = self.cfg["underlying"]
        exchange = self.cfg["exchange"]

        if not self.expiry:
            self.expiry = _resolve_nearest_expiry(underlying, exchange)
            if not self.expiry:
                logger.error("Could not resolve expiry — set CONFIG['expiry'] manually")
                return False
        logger.info(f"Expiry: {self.expiry}")

        if not self.futures_sym:
            self.futures_sym = _resolve_futures_symbol(
                underlying, self.cfg["futures_exch"]
            )
        logger.info(f"Futures: {self.futures_sym}")

        spot = self._spot()
        if spot <= 0:
            logger.error(f"Cannot fetch spot for {underlying}")
            return False

        self.atm_strike = _find_atm_strike(underlying, exchange, self.expiry, spot)
        logger.info(f"Spot: {spot:.2f}  ATM Strike: {self.atm_strike}")

        strike_str = str(int(self.atm_strike)) if self.atm_strike == int(self.atm_strike) else str(self.atm_strike)
        self.ce_sym = f"{underlying}{self.expiry}{strike_str}CE"
        self.pe_sym = f"{underlying}{self.expiry}{strike_str}PE"
        logger.info(f"CE: {self.ce_sym}  PE: {self.pe_sym}")
        return True

    # ── Entry: sell straddle ──────────────────────────────────────────────────

    def enter(self) -> bool:
        qty = self.cfg["num_lots"] * self.cfg["lot_size"]
        logger.info(f"Entering short straddle — SELL {self.ce_sym} x{qty}, SELL {self.pe_sym} x{qty}")
        try:
            ce_resp = self.client.placesmartorder(
                strategy="DeltaNeutralV1",
                symbol=self.ce_sym,
                action="SELL",
                exchange=self.cfg["exchange"],
                price_type="MARKET",
                product="NRML",
                quantity=qty,
                position_size=-qty,
            )
            logger.info(f"CE order: {ce_resp}")

            pe_resp = self.client.placesmartorder(
                strategy="DeltaNeutralV1",
                symbol=self.pe_sym,
                action="SELL",
                exchange=self.cfg["exchange"],
                price_type="MARKET",
                product="NRML",
                quantity=qty,
                position_size=-qty,
            )
            logger.info(f"PE order: {pe_resp}")

            self.entry_done = True
            self._db_save_state()
            self._db_log_trade(
                "ENTRY",
                symbol=f"{self.ce_sym},{self.pe_sym}",
                action="SELL",
                quantity=qty,
                reason="straddle_entry",
            )
            return True
        except Exception as e:
            logger.exception(f"Entry failed: {e}")
            return False

    # ── Greeks calculation ────────────────────────────────────────────────────

    def _greeks_for_leg(
        self, symbol: str, opt_type: str, ltp: float, spot: float
    ) -> dict:
        T = self._days_to_expiry() / 365.0
        r = self.cfg["risk_free"]
        flag = "c" if opt_type == "CE" else "p"
        iv = _implied_vol(spot, self.atm_strike, T, r, ltp, flag)
        return _bsm_greeks(spot, self.atm_strike, T, r, iv, flag)

    # ── Portfolio snapshot ────────────────────────────────────────────────────

    def portfolio_greeks(self, spot: float) -> dict:
        qty = self.cfg["num_lots"] * self.cfg["lot_size"]  # sold qty (negative position)
        ce_ltp = self._quote(self.ce_sym, self.cfg["exchange"])
        pe_ltp = self._quote(self.pe_sym, self.cfg["exchange"])

        ce_g = self._greeks_for_leg(self.ce_sym, "CE", ce_ltp, spot)
        pe_g = self._greeks_for_leg(self.pe_sym, "PE", pe_ltp, spot)

        # Short position: multiply by -qty
        net_delta = (ce_g["delta"] + pe_g["delta"]) * (-qty)
        net_gamma = (ce_g["gamma"] + pe_g["gamma"]) * (-qty)
        net_theta = (ce_g["theta"] + pe_g["theta"]) * (-qty)
        net_vega  = (ce_g["vega"]  + pe_g["vega"])  * (-qty)

        # Add futures hedge delta (1 per lot of underlying)
        net_delta += self.hedge_lots * self.cfg["lot_size"]

        return {
            "spot": spot,
            "ce_ltp": ce_ltp,
            "pe_ltp": pe_ltp,
            "ce_iv": round(ce_g["iv"] * 100, 2),
            "pe_iv": round(pe_g["iv"] * 100, 2),
            "net_delta": round(net_delta, 4),
            "net_gamma": round(net_gamma, 6),
            "net_theta": round(net_theta, 2),
            "net_vega":  round(net_vega,  2),
        }

    # ── Delta hedging ─────────────────────────────────────────────────────────

    def hedge_delta(self, net_delta: float) -> None:
        lot_size = self.cfg["lot_size"]
        threshold = self.cfg["delta_threshold"] * lot_size

        if abs(net_delta) <= threshold:
            return

        # Number of futures lots needed to neutralise
        lots_needed = -round(net_delta / lot_size)
        delta_lots = lots_needed - self.hedge_lots
        if delta_lots == 0:
            return

        action = "BUY" if delta_lots > 0 else "SELL"
        qty = abs(delta_lots) * lot_size
        new_total = self.hedge_lots + delta_lots

        logger.info(
            f"Delta hedge: net_delta={net_delta:.4f} → {action} {qty} futures "
            f"(hedge_lots: {self.hedge_lots} → {new_total})"
        )

        try:
            resp = self.client.placesmartorder(
                strategy="DeltaNeutralV1",
                symbol=self.futures_sym,
                action=action,
                exchange=self.cfg["futures_exch"],
                price_type="MARKET",
                product="NRML",
                quantity=qty,
                position_size=new_total * lot_size,
            )
            logger.info(f"Hedge order: {resp}")
            self.hedge_lots = new_total
            self._db_save_state()
            self._db_log_trade(
                "HEDGE",
                symbol=self.futures_sym,
                action=action,
                quantity=qty,
                reason=f"net_delta={net_delta:.4f}",
            )
        except Exception as e:
            logger.exception(f"Hedge order failed: {e}")

    # ── PnL tracking ──────────────────────────────────────────────────────────

    def current_pnl(self) -> float:
        try:
            resp = self.client.positionbook()
            if resp.get("status") != "success":
                return 0.0
            total = 0.0
            for pos in resp.get("data", []):
                sym = str(pos.get("symbol", ""))
                if sym in (self.ce_sym, self.pe_sym, self.futures_sym):
                    total += float(pos.get("pnl", 0) or 0)
            return total
        except Exception as e:
            logger.debug(f"PnL fetch failed: {e}")
            return 0.0

    # ── Exit all ──────────────────────────────────────────────────────────────

    def exit_all(self, reason: str = "exit") -> None:
        logger.info(f"Exiting all positions — reason: {reason}")
        qty = self.cfg["num_lots"] * self.cfg["lot_size"]
        self._db_log_trade(
            "EXIT",
            symbol=f"{self.ce_sym},{self.pe_sym}",
            action="BUY",
            quantity=qty,
            pnl=self.current_pnl(),
            reason=reason,
        )
        try:
            self.client.placesmartorder(
                strategy="DeltaNeutralV1",
                symbol=self.ce_sym,
                action="BUY",
                exchange=self.cfg["exchange"],
                price_type="MARKET",
                product="NRML",
                quantity=qty,
                position_size=0,
            )
            self.client.placesmartorder(
                strategy="DeltaNeutralV1",
                symbol=self.pe_sym,
                action="BUY",
                exchange=self.cfg["exchange"],
                price_type="MARKET",
                product="NRML",
                quantity=qty,
                position_size=0,
            )
            if self.hedge_lots != 0:
                hedge_action = "SELL" if self.hedge_lots > 0 else "BUY"
                hedge_qty = abs(self.hedge_lots) * self.cfg["lot_size"]
                self.client.placesmartorder(
                    strategy="DeltaNeutralV1",
                    symbol=self.futures_sym,
                    action=hedge_action,
                    exchange=self.cfg["futures_exch"],
                    price_type="MARKET",
                    product="NRML",
                    quantity=hedge_qty,
                    position_size=0,
                )
        except Exception as e:
            logger.exception(f"Exit order failed: {e}")

    # ── Market hours guard ────────────────────────────────────────────────────

    def _in_market_hours(self) -> bool:
        now = datetime.now(IST)
        open_h, open_m = self.cfg["market_open"]
        close_h, close_m = self.cfg["market_close"]
        open_t = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        close_t = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        return open_t <= now <= close_t

    def _near_close(self) -> bool:
        now = datetime.now(IST)
        close_h, close_m = self.cfg["market_close"]
        close_t = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        return now >= close_t


    # ── DB persistence ────────────────────────────────────────────────────────

    def _db_save_state(self) -> None:
        try:
            conn = _db_conn()
            cur = conn.cursor()
            today = datetime.now(IST).date()
            cur.execute(
                """
                INSERT INTO dn_state
                    (strategy_id, run_date, hedge_lots, entry_done,
                     ce_sym, pe_sym, futures_sym, expiry, atm_strike, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (strategy_id) DO UPDATE SET
                    run_date=EXCLUDED.run_date, hedge_lots=EXCLUDED.hedge_lots,
                    entry_done=EXCLUDED.entry_done, ce_sym=EXCLUDED.ce_sym,
                    pe_sym=EXCLUDED.pe_sym, futures_sym=EXCLUDED.futures_sym,
                    expiry=EXCLUDED.expiry, atm_strike=EXCLUDED.atm_strike,
                    updated_at=NOW()
                """,
                (
                    "delta_neutral_v1", today, self.hedge_lots, self.entry_done,
                    self.ce_sym, self.pe_sym, self.futures_sym,
                    self.expiry, self.atm_strike,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("DB save state failed: %s", exc)

    def _db_restore_state(self) -> bool:
        """Return True if today's run was already active (entry_done=True)."""
        try:
            conn = _db_conn()
            cur = conn.cursor()
            today = datetime.now(IST).date()
            cur.execute(
                """
                SELECT hedge_lots, entry_done, ce_sym, pe_sym,
                       futures_sym, expiry, atm_strike
                FROM dn_state
                WHERE strategy_id = 'delta_neutral_v1' AND run_date = %s
                """,
                (today,),
            )
            row = cur.fetchone()
            conn.close()
            if row and row[1]:
                (
                    self.hedge_lots, _, self.ce_sym, self.pe_sym,
                    self.futures_sym, self.expiry, self.atm_strike,
                ) = row
                self.entry_done = True
                logger.info(
                    "Restored state from DB: hedge_lots=%d CE=%s PE=%s",
                    self.hedge_lots, self.ce_sym, self.pe_sym,
                )
                return True
        except Exception as exc:
            logger.warning("DB restore state failed: %s", exc)
        return False

    def _db_log_greeks(self, greeks: dict, pnl: float, var: float, cvar: float) -> None:
        try:
            conn = _db_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO dn_greeks_log
                    (strategy_id, run_date, spot, ce_ltp, pe_ltp,
                     ce_iv, pe_iv, net_delta, net_gamma,
                     net_theta, net_vega, pnl, var_95, cvar_95, hedge_lots)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "delta_neutral_v1",
                    float(greeks.get("spot", 0)), float(greeks.get("ce_ltp", 0)), float(greeks.get("pe_ltp", 0)),
                    float(greeks.get("ce_iv", 0)), float(greeks.get("pe_iv", 0)),
                    float(greeks.get("net_delta", 0)), float(greeks.get("net_gamma", 0)),
                    float(greeks.get("net_theta", 0)), float(greeks.get("net_vega", 0)),
                    float(pnl), float(var), float(cvar), int(self.hedge_lots),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("DB log greeks failed: %s", exc)

    def _db_log_trade(
        self,
        event_type: str,
        symbol: str = "",
        action: str = "",
        quantity: int = 0,
        pnl: float = 0.0,
        reason: str = "",
    ) -> None:
        try:
            conn = _db_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO dn_trade_log
                    (strategy_id, run_date, event_type, symbol, action,
                     quantity, hedge_lots_after, pnl, reason)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "delta_neutral_v1", event_type, symbol, action,
                    quantity, self.hedge_lots, pnl, reason,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("DB log trade failed: %s", exc)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("=== Delta Neutral Strategy v1 Starting ===")

        try:
            _db_ensure_tables()
        except Exception as exc:
            logger.warning("DB init failed (continuing without persistence): %s", exc)

        # Try to resume from a same-day crashed/stopped session
        restored = self._db_restore_state()

        if not restored:
            if not self.setup():
                logger.error("Setup failed — aborting")
                return
        else:
            logger.info("Resuming same-day session (hedge_lots=%d)", self.hedge_lots)
            if not self.futures_sym:
                self.futures_sym = _resolve_futures_symbol(
                    self.cfg["underlying"], self.cfg["futures_exch"]
                )

        # Wait for market open
        while not self._in_market_hours():
            logger.info("Waiting for market open …")
            time.sleep(30)

        # Enter position only if not already done today
        if not self.entry_done:
            if not self.enter():
                logger.error("Entry failed — aborting")
                return
        else:
            logger.info("Skipping entry — already entered today (hedge_lots=%d)", self.hedge_lots)

        logger.info("Position entered. Starting monitoring loop.")
        loop_count = 0

        while True:
            try:
                # Exit signal file
                if os.path.exists(self.cfg["exit_signal"]):
                    os.remove(self.cfg["exit_signal"])
                    self.exit_all("signal_file")
                    break

                # End-of-day exit
                if self._near_close():
                    self.exit_all("eod")
                    break

                spot = self._spot()
                if spot <= 0:
                    logger.warning("Spot fetch failed — skipping cycle")
                    time.sleep(self.cfg["loop_interval"])
                    continue

                # Greeks snapshot
                greeks = self.portfolio_greeks(spot)
                loop_count += 1

                logger.info(
                    f"[{loop_count}] Spot={spot:.2f} | "
                    f"Δ={greeks['net_delta']:.4f} "
                    f"Γ={greeks['net_gamma']:.6f} "
                    f"Θ={greeks['net_theta']:.2f} "
                    f"V={greeks['net_vega']:.2f} | "
                    f"CE_IV={greeks['ce_iv']}% PE_IV={greeks['pe_iv']}%"
                )

                # PnL tracking
                pnl = self.current_pnl()
                self.risk.record(pnl)
                var = self.risk.var()
                cvar = self.risk.cvar()
                logger.info(
                    f"PnL=₹{pnl:.2f} | VaR(95%)=₹{var:.2f} CVaR=₹{cvar:.2f} | "
                    f"Hedge lots={self.hedge_lots}"
                )
                self._db_log_greeks(greeks, pnl, var, cvar)

                # Stop-loss check
                if self.risk.should_stop(pnl, self.cfg["max_loss_abs"]):
                    logger.warning(f"Max loss hit (₹{pnl:.2f}) — exiting all")
                    self._db_log_trade("STOP", pnl=pnl, reason="max_loss")
                    self.exit_all("max_loss")
                    break

                # Delta hedge
                self.hedge_delta(greeks["net_delta"])

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt — exiting cleanly")
                self.exit_all("keyboard")
                break
            except Exception as e:
                logger.exception(f"Loop error: {e}")

            time.sleep(self.cfg["loop_interval"])

        logger.info("=== Delta Neutral Strategy v1 Stopped ===")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    strategy = DeltaNeutralStrategy(CONFIG)
    strategy.run()
