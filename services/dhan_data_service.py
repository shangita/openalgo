"""
Dhan historical data service — standalone, no OpenAlgo broker session needed.
Credentials stored in config/dhan_config.json (client_id + access_token).
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

import httpx
import pandas as pd

from database.token_db import get_token
from utils.logging import get_logger

logger = get_logger(__name__)

_CONFIG_FILE = Path(__file__).parent.parent / "config" / "dhan_config.json"
_BASE_URL = "https://api.dhan.co"

# Rate limiter: Dhan allows 1 req/sec
_lock = threading.Lock()
_last_call: float = 0.0


# ── Credentials ───────────────────────────────────────────────────────────────

def load_credentials() -> dict:
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Dhan config read error: %s", exc)
        return {}


def save_credentials(client_id: str, access_token: str) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        json.dump({"client_id": client_id.strip(), "access_token": access_token.strip()}, f, indent=2)
    logger.info("Dhan credentials saved")


def is_configured() -> bool:
    c = load_credentials()
    return bool(c.get("client_id") and c.get("access_token"))


# ── HTTP layer ────────────────────────────────────────────────────────────────

def _request(endpoint: str, client_id: str, access_token: str, payload: dict) -> dict:
    global _last_call
    with _lock:
        elapsed = time.monotonic() - _last_call
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        _last_call = time.monotonic()

    headers = {
        "access-token": access_token,
        "client-id": client_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = _BASE_URL + endpoint
    with httpx.Client(timeout=30) as c:
        resp = c.post(url, headers=headers, content=json.dumps(payload))
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "failed":
        err = data.get("data", {})
        code = next(iter(err), "unknown")
        msg = err.get(code, "Unknown Dhan API error")
        raise RuntimeError(f"Dhan API error {code}: {msg}")
    return data


# ── Symbol helpers ────────────────────────────────────────────────────────────

_EXCHANGE_SEG = {
    "NSE": "NSE_EQ", "BSE": "BSE_EQ",
    "NFO": "NSE_FNO", "BFO": "BSE_FNO",
    "MCX": "MCX_COMM", "CDS": "NSE_CURRENCY", "BCD": "BSE_CURRENCY",
    "NSE_INDEX": "IDX_I", "BSE_INDEX": "IDX_I",
}

_IDX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
              "SENSEX", "BANKEX", "SENSEX50", "INDIAVIX"}


def _instrument_type(exchange: str, symbol: str) -> str:
    if exchange in ("NSE", "BSE"):
        return "EQUITY"
    if exchange in ("NSE_INDEX", "BSE_INDEX"):
        return "INDEX"
    if exchange in ("NFO", "BFO"):
        is_idx = any(i in symbol for i in _IDX_NAMES)
        if symbol.endswith("CE") or symbol.endswith("PE"):
            return "OPTIDX" if is_idx else "OPTSTK"
        return "FUTIDX" if is_idx else "FUTSTK"
    if exchange == "MCX":
        return "OPTFUT" if symbol.endswith(("CE", "PE")) else "FUTCOM"
    if exchange in ("CDS", "BCD"):
        return "OPTCUR" if symbol.endswith(("CE", "PE")) else "FUTCUR"
    raise ValueError(f"Unsupported exchange: {exchange}")


_INTERVAL_MAP = {"1m": "1", "5m": "5", "15m": "15", "25m": "25", "1h": "60", "D": "D"}


def _date_chunks(start: str, end: str, days: int = 90) -> list[tuple[str, str]]:
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    chunks = []
    while s < e:
        ce = min(s + timedelta(days=days), e)
        chunks.append((s.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")))
        s = ce
    return chunks


def _next_trading_day(dt: datetime, forward: bool) -> datetime:
    step = timedelta(days=1 if forward else -1)
    while dt.weekday() >= 5:
        dt += step
    return dt


# ── Main fetch function ───────────────────────────────────────────────────────

def get_dhan_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
) -> Tuple[bool, pd.DataFrame | str]:
    """
    Fetch OHLCV data from Dhan API directly.
    Returns (True, DataFrame) on success, (False, error_message) on failure.
    DataFrame columns: timestamp (unix int), open, high, low, close, volume
    """
    creds = load_credentials()
    if not creds.get("client_id") or not creds.get("access_token"):
        return False, "Dhan credentials not configured. Go to Historify → Download Settings → Data Source → configure Dhan."

    if interval not in _INTERVAL_MAP:
        return False, f"Dhan unsupported interval '{interval}'. Supported: {', '.join(_INTERVAL_MAP)}"

    seg = _EXCHANGE_SEG.get(exchange.upper())
    if not seg:
        return False, f"Unsupported exchange for Dhan: {exchange}"

    security_id = get_token(symbol, exchange.upper())
    if not security_id:
        return False, f"Symbol '{symbol}' not found in master contract for {exchange}"

    try:
        instr = _instrument_type(exchange.upper(), symbol.upper())
    except ValueError as exc:
        return False, str(exc)

    client_id = creds["client_id"]
    access_token = creds["access_token"]

    # Adjust weekend dates
    s_dt = _next_trading_day(datetime.strptime(start_date, "%Y-%m-%d"), forward=True)
    e_dt = _next_trading_day(datetime.strptime(end_date, "%Y-%m-%d"), forward=False)
    start_date = s_dt.strftime("%Y-%m-%d")
    end_date = e_dt.strftime("%Y-%m-%d")

    all_candles: list[dict] = []

    try:
        if interval == "D":
            end_plus = (e_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            payload = {
                "securityId": str(security_id),
                "exchangeSegment": seg,
                "instrument": instr,
                "fromDate": start_date,
                "toDate": end_plus,
                "oi": True,
                "expiryCode": 0,
            }
            resp = _request("/v2/charts/historical", client_id, access_token, payload)
            ts_list = resp.get("timestamp", [])
            opens = resp.get("open", [])
            highs = resp.get("high", [])
            lows  = resp.get("low", [])
            closes = resp.get("close", [])
            vols  = resp.get("volume", [])
            for i in range(len(ts_list)):
                # UTC → IST (daily: align to IST date start)
                utc_dt = datetime.utcfromtimestamp(ts_list[i])
                ist_dt = utc_dt + timedelta(hours=5, minutes=30)
                day_ts = int(datetime(ist_dt.year, ist_dt.month, ist_dt.day).timestamp()) + 19800
                all_candles.append({
                    "timestamp": day_ts,
                    "open":   float(opens[i] or 0),
                    "high":   float(highs[i] or 0),
                    "low":    float(lows[i] or 0),
                    "close":  float(closes[i] or 0),
                    "volume": int(float(vols[i] or 0)),
                })
        else:
            for chunk_s, chunk_e in _date_chunks(start_date, end_date):
                chunk_e_plus = (datetime.strptime(chunk_e, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                payload = {
                    "securityId": str(security_id),
                    "exchangeSegment": seg,
                    "instrument": instr,
                    "interval": _INTERVAL_MAP[interval],
                    "fromDate": chunk_s,
                    "toDate": chunk_e_plus,
                    "oi": True,
                    "expiryCode": 0,
                }
                resp = _request("/v2/charts/intraday", client_id, access_token, payload)
                ts_list = resp.get("timestamp", [])
                opens  = resp.get("open", [])
                highs  = resp.get("high", [])
                lows   = resp.get("low", [])
                closes = resp.get("close", [])
                vols   = resp.get("volume", [])
                for i in range(len(ts_list)):
                    utc_dt = datetime.utcfromtimestamp(ts_list[i])
                    ist_dt = utc_dt + timedelta(hours=5, minutes=30)
                    ist_ts = int(ist_dt.timestamp())
                    all_candles.append({
                        "timestamp": ist_ts,
                        "open":   float(opens[i] or 0),
                        "high":   float(highs[i] or 0),
                        "low":    float(lows[i] or 0),
                        "close":  float(closes[i] or 0),
                        "volume": int(float(vols[i] or 0)),
                    })

        if not all_candles:
            return True, pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(all_candles)
        df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        logger.info("Dhan: fetched %d candles for %s %s %s", len(df), symbol, exchange, interval)
        return True, df

    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        logger.exception("Dhan fetch error: %s", exc)
        return False, f"Dhan fetch failed: {exc}"
