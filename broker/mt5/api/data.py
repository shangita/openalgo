"""
MT5 Broker Plugin - Data API
================================
Historical candles, quotes, and multi-quotes via Windows VPS bridge.
"""

import os
import requests
from utils.logging import get_logger

logger = get_logger(__name__)


def _data_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_DATA_PORT", "5001")
    return "http://%s:%s" % (ip, port)


def _executor_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_EXECUTOR_PORT", "5000")
    return "http://%s:%s" % (ip, port)


def _headers(auth):
    return {"X-API-Key": auth, "Content-Type": "application/json"}


class BrokerData:
    def __init__(self, auth_token):
        self.auth_token = auth_token

        # Map OpenAlgo intervals to MT5 timeframe strings
        self.timeframe_map = {
            "1m": "M1",
            "2m": "M2",
            "3m": "M3",
            "5m": "M5",
            "10m": "M10",
            "15m": "M15",
            "30m": "M30",
            "60m": "H1",
            "1h": "H1",
            "2h": "H2",
            "4h": "H4",
            "D": "D1",
            "1d": "D1",
            "W": "W1",
            "1w": "W1",
        }

        # Forex markets are nearly 24/5
        self.market_timings = {
            "FOREX": {"start": "00:00:00", "end": "23:59:59"},
            "CFD": {"start": "00:00:00", "end": "23:59:59"},
            "COMMODITY": {"start": "00:00:00", "end": "23:59:59"},
            "CRYPTO": {"start": "00:00:00", "end": "23:59:59"},
            "INDEX": {"start": "00:00:00", "end": "23:59:59"},
        }

        self.default_market_timings = {"start": "00:00:00", "end": "23:59:59"}

    def get_market_timings(self, exchange):
        return self.market_timings.get(exchange, self.default_market_timings)

    def get_quotes(self, symbol, exchange):
        """Get real-time quote for a symbol."""
        try:
            url = "%s/quote?symbol=%s" % (_data_url(), symbol)
            r = requests.get(url, headers=_headers(self.auth_token), timeout=10)
            r.raise_for_status()
            data = r.json()

            return {
                "ask": data.get("ask", 0),
                "bid": data.get("bid", 0),
                "high": data.get("high", 0),
                "low": data.get("low", 0),
                "ltp": data.get("ltp", 0),
                "open": data.get("open", 0),
                "prev_close": data.get("prev_close", 0),
                "volume": data.get("volume", 0),
                "oi": 0,  # No OI in forex
            }

        except Exception as e:
            logger.exception("Error fetching quote for %s", symbol)
            raise Exception("Error fetching quotes: %s" % str(e))

    def get_multiquotes(self, symbols):
        """Get quotes for multiple symbols."""
        results = []
        for item in symbols:
            try:
                quote = self.get_quotes(item["symbol"], item.get("exchange", "FOREX"))
                results.append({
                    "symbol": item["symbol"],
                    "exchange": item.get("exchange", "FOREX"),
                    "data": quote,
                })
            except Exception as e:
                results.append({
                    "symbol": item["symbol"],
                    "exchange": item.get("exchange", "FOREX"),
                    "data": None,
                    "error": str(e),
                })
        return results

    def get_history(self, symbol, exchange, interval, start_date, end_date):
        """
        Fetch historical OHLCV candle data.

        Returns list of dicts with: timestamp, open, high, low, close, volume
        """
        mt5_tf = self.timeframe_map.get(interval, interval)

        try:
            url = "%s/history" % _data_url()
            payload = {
                "symbol": symbol,
                "interval": mt5_tf,
                "start_date": start_date,
                "end_date": end_date,
            }
            r = requests.post(url, json=payload, headers=_headers(self.auth_token), timeout=30)
            r.raise_for_status()
            data = r.json()
            return data.get("data", [])

        except Exception as e:
            logger.exception("Error fetching history for %s %s", symbol, interval)
            raise Exception("Error fetching history: %s" % str(e))

    def get_intraday(self, symbol, exchange, interval):
        """
        Fetch intraday candle data (today only).
        Uses the /candles endpoint with a reasonable count.
        """
        mt5_tf = self.timeframe_map.get(interval, interval)

        # Calculate approximate bar count for today based on interval
        bar_counts = {
            "M1": 1440, "M5": 288, "M15": 96, "M30": 48,
            "H1": 24, "H4": 6, "D1": 1,
        }
        count = bar_counts.get(mt5_tf, 100)

        try:
            url = "%s/candles?symbol=%s&timeframe=%s&count=%d" % (
                _data_url(), symbol, mt5_tf, count
            )
            r = requests.get(url, headers=_headers(self.auth_token), timeout=15)
            r.raise_for_status()
            data = r.json()

            candles = data.get("candles", [])
            # Rename 'time' to 'timestamp' for OpenAlgo compatibility
            for c in candles:
                if "time" in c and "timestamp" not in c:
                    c["timestamp"] = c.pop("time")

            return candles

        except Exception as e:
            logger.exception("Error fetching intraday for %s", symbol)
            raise Exception("Error fetching intraday: %s" % str(e))
