"""DB helpers for delta neutral strategy — state, Greeks log, trade log."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

IST = ZoneInfo("Asia/Kolkata")
_STRAT_ID = "delta_neutral_v1"


def _conn():
    return psycopg2.connect(
        dbname="openalgo", user="trader", password="trader", host="127.0.0.1"
    )


def get_dn_state() -> dict | None:
    """Return today's dn_state row, or None if not found."""
    try:
        conn = _conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        today = datetime.now(IST).date()
        cur.execute(
            "SELECT * FROM dn_state WHERE strategy_id = %s AND run_date = %s",
            (_STRAT_ID, today),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["run_date"] = str(d["run_date"])
            if d.get("updated_at"):
                d["updated_at"] = d["updated_at"].isoformat()
            return d
    except Exception:
        pass
    return None


def get_dn_greeks(limit: int = 120) -> list[dict]:
    """Return today's Greeks snapshots (chronological, newest last)."""
    try:
        conn = _conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        today = datetime.now(IST).date()
        cur.execute(
            """
            SELECT ts, spot, ce_ltp, pe_ltp, ce_iv, pe_iv,
                   net_delta, net_gamma, net_theta, net_vega,
                   pnl, var_95, cvar_95, hedge_lots
            FROM dn_greeks_log
            WHERE strategy_id = %s AND run_date = %s
            ORDER BY ts DESC
            LIMIT %s
            """,
            (_STRAT_ID, today, limit),
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("ts"):
                d["ts"] = d["ts"].isoformat()
            result.append(d)
        return list(reversed(result))
    except Exception:
        return []


def get_dn_trades() -> list[dict]:
    """Return today's trade/event log (chronological)."""
    try:
        conn = _conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        today = datetime.now(IST).date()
        cur.execute(
            """
            SELECT ts, event_type, symbol, action, quantity,
                   hedge_lots_after, pnl, reason
            FROM dn_trade_log
            WHERE strategy_id = %s AND run_date = %s
            ORDER BY ts ASC
            """,
            (_STRAT_ID, today),
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("ts"):
                d["ts"] = d["ts"].isoformat()
            result.append(d)
        return result
    except Exception:
        return []
