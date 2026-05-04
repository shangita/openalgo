"""
Nifty 50 universe — plain tradingsymbol + exchange, matching OpenAlgo's format.
"""
from __future__ import annotations

NIFTY50: list[tuple[str, str]] = [
    ("RELIANCE", "NSE"),
    ("TCS", "NSE"),
    ("HDFCBANK", "NSE"),
    ("BHARTIARTL", "NSE"),
    ("ICICIBANK", "NSE"),
    ("INFY", "NSE"),
    ("SBIN", "NSE"),
    ("HINDUNILVR", "NSE"),
    ("ITC", "NSE"),
    ("LT", "NSE"),
    ("BAJFINANCE", "NSE"),
    ("KOTAKBANK", "NSE"),
    ("HCLTECH", "NSE"),
    ("MARUTI", "NSE"),
    ("AXISBANK", "NSE"),
    ("ASIANPAINT", "NSE"),
    ("SUNPHARMA", "NSE"),
    ("ULTRACEMCO", "NSE"),
    ("TITAN", "NSE"),
    ("WIPRO", "NSE"),
    ("ONGC", "NSE"),
    ("NTPC", "NSE"),
    ("JSWSTEEL", "NSE"),
    ("TATASTEEL", "NSE"),
    ("POWERGRID", "NSE"),
    ("M&M", "NSE"),
    ("BAJAJFINSV", "NSE"),
    ("NESTLEIND", "NSE"),
    ("TECHM", "NSE"),
    ("ADANIENT", "NSE"),
    ("ADANIPORTS", "NSE"),
    ("COALINDIA", "NSE"),
    ("DIVISLAB", "NSE"),
    ("DRREDDY", "NSE"),
    ("EICHERMOT", "NSE"),
    ("GRASIM", "NSE"),
    ("HDFCLIFE", "NSE"),
    ("HEROMOTOCO", "NSE"),
    ("HINDALCO", "NSE"),
    ("INDUSINDBK", "NSE"),
    ("SBILIFE", "NSE"),
    ("SHRIRAMFIN", "NSE"),
    ("TATACONSUM", "NSE"),
    ("TMCV", "NSE"),
    ("TRENT", "NSE"),
    ("BPCL", "NSE"),
    ("CIPLA", "NSE"),
    ("BRITANNIA", "NSE"),
    ("APOLLOHOSP", "NSE"),
    ("BEL", "NSE"),
]

def get_universe(name: str = "nifty50") -> list[tuple[str, str]]:
    if name.lower() in ("nifty50", "nifty_50"):
        return list(NIFTY50)
    raise ValueError(f"Unknown universe: {name}")
