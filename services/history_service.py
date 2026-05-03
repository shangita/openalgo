import re
import importlib
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from database.auth_db import get_auth_token_broker
from database.token_db import get_token
from utils.constants import VALID_EXCHANGES
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Rate limiter: max 3 broker history API requests per second
# Uses minimum interval between calls to prevent burst requests
_last_history_call: float = 0.0
_MIN_HISTORY_INTERVAL = 0.35  # 350ms between calls (~3 req/sec, evenly spaced)


def _enforce_rate_limit():
    """Block until enough time has passed since the last request (~3 per second)."""
    global _last_history_call
    now = time.monotonic()
    elapsed = now - _last_history_call
    if elapsed < _MIN_HISTORY_INTERVAL:
        time.sleep(_MIN_HISTORY_INTERVAL - elapsed)
    _last_history_call = time.monotonic()


def validate_symbol_exchange(symbol: str, exchange: str) -> tuple[bool, str | None]:
    """
    Validate that a symbol exists for the given exchange.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, NFO)

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Validate exchange
    exchange_upper = exchange.upper()
    if exchange_upper not in VALID_EXCHANGES:
        return False, f"Invalid exchange '{exchange}'. Must be one of: {', '.join(VALID_EXCHANGES)}"

    # Validate symbol exists in master contract
    token = get_token(symbol, exchange_upper)
    if token is None:
        return (
            False,
            f"Symbol '{symbol}' not found for exchange '{exchange}'. Please verify the symbol name and ensure master contracts are downloaded.",
        )

    return True, None


def import_broker_module(broker_name: str) -> Any | None:
    """
    Dynamically import the broker-specific data module.

    Args:
        broker_name: Name of the broker

    Returns:
        The imported module or None if import fails
    """
    try:
        module_path = f"broker.{broker_name}.api.data"
        broker_module = importlib.import_module(module_path)
        return broker_module
    except ImportError as error:
        logger.error(f"Error importing broker module '{module_path}': {error}")
        return None



# ─── yfinance fallback symbol map ────────────────────────────────────────────
_YF_BASE_MAP = {
    'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK', 'FINNIFTY': 'NIFTY_FIN_SERVICE.NS',
    'HDFCBANK': 'HDFCBANK.NS', 'RELIANCE': 'RELIANCE.NS', 'TCS': 'TCS.NS',
    'INFY': 'INFY.NS', 'SBIN': 'SBIN.NS', 'ICICIBANK': 'ICICIBANK.NS',
    'SILVERM': 'SI=F', 'SILVER': 'SI=F', 'GOLD': 'GC=F', 'GOLDM': 'GC=F',
    'CRUDEOIL': 'CL=F', 'NATURALGAS': 'NG=F', 'COPPER': 'HG=F',
}
_YF_INTERVAL_MAP = {'1m':'1m','3m':'5m','5m':'5m','10m':'15m','15m':'15m','30m':'30m','1h':'1h','1d':'1d','D':'1d'}

def _extract_base_symbol(symbol: str) -> str:
    """Strip expiry from futures symbol e.g. HDFCBANK28APR26FUT -> HDFCBANK"""
    return re.sub(r'[0-9]{2}[A-Z]{3}[0-9]{2}FUT$', '', symbol).rstrip('0123456789')

def _get_yf_symbol(symbol: str, exchange: str) -> str | None:
    """Map OpenAlgo symbol/exchange to yfinance ticker."""
    base = _extract_base_symbol(symbol.upper())
    if base in _YF_BASE_MAP:
        return _YF_BASE_MAP[base]
    if exchange in ('NSE', 'NFO', 'BSE', 'BFO'):
        return f"{base}.NS"
    return None

def get_history_from_yfinance(
    symbol: str, exchange: str, interval: str, start_date: str, end_date: str
) -> tuple[bool, dict, int]:
    """Fallback to yfinance when broker API returns permission error."""
    try:
        import yfinance as yf
        from datetime import datetime as _dt, timedelta as _td
        import pytz as _pytz
        yf_sym = _get_yf_symbol(symbol, exchange)
        if not yf_sym:
            logger.warning("No yfinance mapping for %s/%s", symbol, exchange)
            return False, {"status": "error", "message": f"No yfinance mapping for {symbol}/{exchange}"}, 500
        yf_interval = _YF_INTERVAL_MAP.get(interval, interval)
        # Use TZ-aware dates to ensure today IST data is included
        ist = _pytz.timezone("Asia/Kolkata")
        try:
            start_dt = ist.localize(_dt.strptime(start_date, "%Y-%m-%d"))
            end_dt = ist.localize(_dt.strptime(end_date, "%Y-%m-%d")) + _td(days=1)
        except Exception:
            start_dt = _dt.now(ist) - _td(days=7)
            end_dt = _dt.now(ist) + _td(days=1)
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(start=start_dt, end=end_dt, interval=yf_interval, auto_adjust=True)
        if df.empty:
            # Try period fallback
            days = max(5, (_dt.now(ist).date() - start_dt.date()).days + 1)
            period = f"{min(days, 7)}d"
            df = ticker.history(period=period, interval=yf_interval, auto_adjust=True)
        if df.empty:
            logger.warning("yfinance returned empty for %s", yf_sym)
            return False, {"status": "error", "message": f"No data from yfinance for {yf_sym}"}, 500
        df.columns = [c.lower() for c in df.columns]
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df["timestamp"] = df.index.tz_convert("UTC").astype("int64") // 10**9
        else:
            df["timestamp"] = df.index.tz_localize("UTC").astype("int64") // 10**9
        df["oi"] = 0
        df = df[["timestamp","open","high","low","close","volume","oi"]].reset_index(drop=True)
        logger.info("yfinance fallback OK: %s -> %s (%d rows)", symbol, yf_sym, len(df))
        return True, {"status": "success", "data": df.to_dict(orient="records")}, 200
    except Exception as e:
        logger.error("yfinance fallback failed for %s: %s", symbol, e)
        return False, {"status": "error", "message": str(e)}, 500

def get_history_with_auth(
    auth_token: str,
    feed_token: str | None,
    broker: str,
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data for a symbol using provided auth tokens.

    Args:
        auth_token: Authentication token for the broker API
        feed_token: Feed token for market data (if required by broker)
        broker: Name of the broker
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, 1d)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Validate symbol and exchange before making broker API call
    is_valid, error_msg = validate_symbol_exchange(symbol, exchange)
    if not is_valid:
        return False, {"status": "error", "message": error_msg}, 400

    broker_module = import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        # Initialize broker's data handler based on broker's requirements
        if hasattr(broker_module.BrokerData.__init__, "__code__"):
            # Check number of parameters the broker's __init__ accepts
            param_count = broker_module.BrokerData.__init__.__code__.co_argcount
            if param_count > 2:  # More than self and auth_token
                data_handler = broker_module.BrokerData(auth_token, feed_token)
            else:
                data_handler = broker_module.BrokerData(auth_token)
        else:
            # Fallback to just auth token if we can't inspect
            data_handler = broker_module.BrokerData(auth_token)

        # Call the broker's get_history method
        df = data_handler.get_history(symbol, exchange, interval, start_date, end_date)

        if not isinstance(df, pd.DataFrame):
            raise ValueError("Invalid data format returned from broker")

        # Ensure all responses include 'oi' field, set to 0 if not present
        if "oi" not in df.columns:
            df["oi"] = 0

        return True, {"status": "success", "data": df.to_dict(orient="records")}, 200
    except Exception as e:
        err_msg = str(e).lower()
        if "permission" in err_msg or "insufficient permission" in err_msg or "incorrect" in err_msg or "access_token" in err_msg or "invalid token" in err_msg:
            logger.warning("Broker permission denied, using yfinance fallback for %s/%s", symbol, exchange)
            return get_history_from_yfinance(symbol, exchange, interval, start_date, end_date)
        logger.error(f"Error in broker_module.get_history: {e}")
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_history_from_db(
    symbol: str, exchange: str, interval: str, start_date: str, end_date: str
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data from DuckDB/Historify database.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, D, W, M, Q, Y)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    try:
        from datetime import date, datetime

        from database.historify_db import get_ohlcv

        # Convert dates to timestamps (handle both string and date objects)
        if isinstance(start_date, date):
            start_dt = datetime.combine(start_date, datetime.min.time())
        else:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")

        if isinstance(end_date, date):
            end_dt = datetime.combine(end_date, datetime.min.time())
        else:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Set end_date to end of day
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

        start_timestamp = int(start_dt.timestamp())
        end_timestamp = int(end_dt.timestamp())

        # Get data from DuckDB
        df = get_ohlcv(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        if df.empty:
            return (
                False,
                {
                    "status": "error",
                    "message": f"No data found for {symbol}:{exchange} interval {interval} in local database. Download data first using Historify.",
                },
                404,
            )

        # Ensure 'oi' column exists
        if "oi" not in df.columns:
            df["oi"] = 0

        # Reorder columns to match API response format
        columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
        df = df[columns]

        return True, {"status": "success", "data": df.to_dict(orient="records")}, 200

    except Exception as e:
        logger.error(f"Error fetching history from DB: {e}")
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_history(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    api_key: str | None = None,
    auth_token: str | None = None,
    feed_token: str | None = None,
    broker: str | None = None,
    source: str = "api",
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data for a symbol.
    Supports both API-based authentication and direct internal calls.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, D, W, M, Q, Y)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        api_key: OpenAlgo API key (for API-based calls)
        auth_token: Direct broker authentication token (for internal calls)
        feed_token: Direct broker feed token (for internal calls)
        broker: Direct broker name (for internal calls)
        source: Data source - 'api' (broker, default) or 'db' (DuckDB/Historify)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Source: 'db' - Fetch from DuckDB/Historify database
    if source == "db":
        return get_history_from_db(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    # Source: 'api' (default) - Fetch from broker API
    # Enforce 3 requests/second rate limit for broker history calls
    _enforce_rate_limit()

    # Case 1: API-based authentication
    if api_key and not (auth_token and broker):
        AUTH_TOKEN, FEED_TOKEN, broker_name = get_auth_token_broker(
            api_key, include_feed_token=True
        )
        if AUTH_TOKEN is None:
            return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
        return get_history_with_auth(
            AUTH_TOKEN, FEED_TOKEN, broker_name, symbol, exchange, interval, start_date, end_date
        )

    # Case 2: Direct internal call with auth_token and broker
    elif auth_token and broker:
        return get_history_with_auth(
            auth_token, feed_token, broker, symbol, exchange, interval, start_date, end_date
        )

    # Case 3: Invalid parameters
    else:
        return (
            False,
            {
                "status": "error",
                "message": "Either api_key or both auth_token and broker must be provided",
            },
            400,
        )
