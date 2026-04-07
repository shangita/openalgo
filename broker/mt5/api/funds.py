"""
MT5 Broker Plugin - Funds/Margin API
=======================================
Account margin and fund data from MT5 VPS bridge.
"""

import os
import requests
from utils.logging import get_logger

logger = get_logger(__name__)


def _executor_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_EXECUTOR_PORT", "5000")
    return "http://%s:%s" % (ip, port)


def _headers(auth):
    return {"X-API-Key": auth}


def get_margin_data(auth_token):
    """
    Fetch margin/fund data from MT5 VPS.

    Returns dict with OpenAlgo standard fields:
        availablecash, collateral, m2munrealized, m2mrealized, utiliseddebits
    """
    url = "%s/margin" % _executor_url()
    try:
        r = requests.get(url, headers=_headers(auth_token), timeout=10)
        r.raise_for_status()
        data = r.json()

        if data.get("status"):
            return data.get("data", {})
        else:
            logger.error("MT5 margin data error: %s", data.get("error"))
            return {
                "availablecash": "0.00",
                "collateral": "0.00",
                "m2munrealized": "0.00",
                "m2mrealized": "0.00",
                "utiliseddebits": "0.00",
            }

    except Exception as e:
        logger.exception("Failed to fetch MT5 margin data")
        return {
            "availablecash": "0.00",
            "collateral": "0.00",
            "m2munrealized": "0.00",
            "m2mrealized": "0.00",
            "utiliseddebits": "0.00",
        }


def test_auth_token(auth_token):
    """
    Verify the auth token (API key) is valid by pinging the VPS.

    Returns:
        (is_valid: bool, error_message: str or None)
    """
    url = "%s/health" % _executor_url()
    try:
        r = requests.get(url, headers=_headers(auth_token), timeout=10)
        if r.status_code == 401:
            return False, "Invalid API key"
        r.raise_for_status()
        data = r.json()

        if data.get("mt5"):
            return True, None
        else:
            return False, "MT5 terminal not connected"

    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to MT5 VPS"
    except Exception as e:
        return False, "Auth test failed: %s" % str(e)
