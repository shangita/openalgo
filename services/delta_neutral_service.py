"""
Delta Neutral Strategy Service
Monitors open option positions, computes portfolio Greeks and payoff chart.
"""

import re
from datetime import datetime
from typing import Any

import pytz

from services.option_greeks_service import calculate_greeks, parse_option_symbol
from services.positionbook_service import get_positionbook
from services.quotes_service import get_quotes
from services.straddle_chart_service import (
    BSE_INDEX_SYMBOLS,
    NSE_INDEX_SYMBOLS,
    _get_quote_exchange,
)
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

OPTION_EXCHANGES = {"NFO", "BFO", "CDS", "MCX"}


def _is_option_symbol(symbol: str) -> bool:
    return bool(re.search(r"\d{2}[A-Z]{3}\d{2}[\d.]+(?:CE|PE)$", symbol.upper()))


def _get_ltp(symbol: str, exchange: str, api_key: str) -> float | None:
    try:
        ok, resp, _ = get_quotes(symbol=symbol, exchange=exchange, api_key=api_key)
        if ok and resp.get("status") == "success":
            return float(resp.get("ltp", 0))
    except Exception as e:
        logger.debug(f"LTP fetch failed for {symbol}: {e}")
    return None


def _normalize_qty(pos: dict) -> int:
    for key in ("quantity", "qty", "netqty", "net_qty"):
        if key in pos:
            try:
                return int(pos[key])
            except (ValueError, TypeError):
                pass
    return 0


def _normalize_avg(pos: dict) -> float:
    for key in ("average_price", "averageprice", "avg_price", "buy_avg", "sell_avg"):
        if key in pos:
            try:
                return float(pos[key])
            except (ValueError, TypeError):
                pass
    return 0.0


def _expiry_matches(symbol: str, expiry_filter: str) -> bool:
    if not expiry_filter:
        return True
    match = re.search(r"(\d{2}[A-Z]{3}\d{2})", symbol.upper())
    return match is not None and match.group(1) == expiry_filter.upper()


def _underlying_matches(symbol: str, underlying: str) -> bool:
    return symbol.upper().startswith(underlying.upper())


def get_delta_neutral_portfolio(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
) -> tuple[bool, dict[str, Any], int]:
    """
    Fetch open option positions for the given underlying/expiry,
    calculate per-leg Greeks and aggregate portfolio Greeks and payoff.
    """
    try:
        ok, pb_resp, _ = get_positionbook(api_key=api_key)
        if not ok or pb_resp.get("status") != "success":
            return False, {"status": "error", "message": "Failed to fetch positionbook"}, 502

        all_positions = pb_resp.get("data", [])
        if not isinstance(all_positions, list):
            all_positions = []

        legs = []
        for pos in all_positions:
            sym = str(pos.get("symbol", ""))
            exch = str(pos.get("exchange", ""))
            qty = _normalize_qty(pos)
            if qty == 0:
                continue
            if exch.upper() != exchange.upper():
                continue
            if not _is_option_symbol(sym):
                continue
            if not _underlying_matches(sym, underlying):
                continue
            if expiry_date and not _expiry_matches(sym, expiry_date):
                continue
            legs.append({
                "symbol": sym,
                "exchange": exch,
                "quantity": qty,
                "average_price": _normalize_avg(pos),
                "ltp": float(pos.get("ltp", 0) or 0),
                "pnl": float(pos.get("pnl", 0) or 0),
            })

        if not legs:
            return True, {
                "status": "success",
                "underlying": underlying,
                "exchange": exchange,
                "expiry_date": expiry_date,
                "spot_price": 0,
                "legs": [],
                "portfolio": {
                    "net_delta": 0, "net_gamma": 0,
                    "net_theta": 0, "net_vega": 0,
                    "net_premium": 0, "total_pnl": 0,
                },
                "payoff": [],
                "breakevens": [],
                "message": "No open option positions found for the given filter",
            }, 200

        quote_exchange = _get_quote_exchange(underlying.upper(), exchange.upper())
        spot_price = 0.0
        ok_q, q_resp, _ = get_quotes(
            symbol=underlying.upper(), exchange=quote_exchange, api_key=api_key
        )
        if ok_q and q_resp.get("status") == "success":
            spot_price = float(q_resp.get("ltp", 0) or 0)

        enriched_legs = []
        net_delta = net_gamma = net_theta = net_vega = 0.0
        net_premium = total_pnl = 0.0

        for leg in legs:
            sym = leg["symbol"]
            qty = leg["quantity"]
            avg_price = leg["average_price"]
            ltp = leg["ltp"]

            try:
                _, expiry_dt, strike, opt_type = parse_option_symbol(sym, exchange)
                days_to_expiry = max(0, (expiry_dt - datetime.now(IST)).total_seconds() / 86400)
            except Exception:
                strike, opt_type, days_to_expiry = 0.0, "CE", 0.0

            if ltp == 0 and spot_price > 0:
                fetched = _get_ltp(sym, exchange, api_key)
                if fetched is not None:
                    ltp = fetched

            leg_delta = leg_gamma = leg_theta = leg_vega = leg_iv = None
            if ltp > 0 and spot_price > 0:
                ok_g, g_resp, _ = calculate_greeks(
                    option_symbol=sym,
                    exchange=exchange,
                    spot_price=spot_price,
                    option_price=ltp,
                    api_key=api_key,
                )
                if ok_g and g_resp.get("status") == "success":
                    gdata = g_resp.get("data", {})
                    leg_delta = gdata.get("delta")
                    leg_gamma = gdata.get("gamma")
                    leg_theta = gdata.get("theta")
                    leg_vega = gdata.get("vega")
                    leg_iv = gdata.get("iv")

            if leg_delta is not None:
                net_delta += leg_delta * qty
            if leg_gamma is not None:
                net_gamma += leg_gamma * qty
            if leg_theta is not None:
                net_theta += leg_theta * qty
            if leg_vega is not None:
                net_vega += leg_vega * qty

            leg_pnl = (ltp - avg_price) * qty
            total_pnl += leg_pnl
            net_premium += avg_price * (-qty)

            enriched_legs.append({
                "symbol": sym,
                "exchange": exchange,
                "option_type": opt_type,
                "strike": round(strike, 2),
                "quantity": qty,
                "average_price": round(avg_price, 2),
                "ltp": round(ltp, 2),
                "days_to_expiry": round(days_to_expiry, 2),
                "iv": round(leg_iv * 100, 2) if leg_iv is not None else None,
                "delta": round(leg_delta, 4) if leg_delta is not None else None,
                "gamma": round(leg_gamma, 6) if leg_gamma is not None else None,
                "theta": round(leg_theta, 2) if leg_theta is not None else None,
                "vega": round(leg_vega, 2) if leg_vega is not None else None,
                "net_delta": round(leg_delta * qty, 4) if leg_delta is not None else None,
                "net_gamma": round(leg_gamma * qty, 6) if leg_gamma is not None else None,
                "net_theta": round(leg_theta * qty, 2) if leg_theta is not None else None,
                "net_vega": round(leg_vega * qty, 2) if leg_vega is not None else None,
                "pnl": round(leg_pnl, 2),
            })

        atm = spot_price if spot_price > 0 else (
            enriched_legs[0]["strike"] if enriched_legs else 0
        )
        spot_range_pct = 0.20
        n_points = 101
        if atm > 0:
            s_min = atm * (1 - spot_range_pct)
            s_max = atm * (1 + spot_range_pct)
        else:
            s_min, s_max = 0, 1
        step = (s_max - s_min) / (n_points - 1)

        payoff = []
        for i in range(n_points):
            S = s_min + i * step
            pnl_at_expiry = 0.0
            for leg in enriched_legs:
                K = leg["strike"]
                qty = leg["quantity"]
                avg_p = leg["average_price"]
                otype = leg["option_type"]
                intrinsic = max(0.0, S - K) if otype == "CE" else max(0.0, K - S)
                if qty < 0:
                    pnl_at_expiry += (avg_p - intrinsic) * (-qty)
                else:
                    pnl_at_expiry += (intrinsic - avg_p) * qty
            payoff.append({"spot": round(S, 2), "pnl": round(pnl_at_expiry, 2)})

        breakevens = []
        for i in range(1, len(payoff)):
            p0, p1 = payoff[i - 1]["pnl"], payoff[i]["pnl"]
            if (p0 < 0 <= p1) or (p0 > 0 >= p1):
                ratio = abs(p0) / (abs(p0) + abs(p1))
                be = payoff[i - 1]["spot"] + ratio * (payoff[i]["spot"] - payoff[i - 1]["spot"])
                breakevens.append(round(be, 2))

        return True, {
            "status": "success",
            "underlying": underlying,
            "exchange": exchange,
            "expiry_date": expiry_date,
            "spot_price": round(spot_price, 2),
            "legs": enriched_legs,
            "portfolio": {
                "net_delta": round(net_delta, 4),
                "net_gamma": round(net_gamma, 6),
                "net_theta": round(net_theta, 2),
                "net_vega": round(net_vega, 2),
                "net_premium": round(net_premium, 2),
                "total_pnl": round(total_pnl, 2),
            },
            "payoff": payoff,
            "breakevens": breakevens,
        }, 200

    except Exception as e:
        logger.exception(f"Delta neutral portfolio error: {e}")
        return False, {"status": "error", "message": "Internal error computing portfolio"}, 500
