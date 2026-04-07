"""
MT5 Broker Plugin - Authentication
====================================
MT5 uses API key auth to the Windows VPS bridge, not OAuth.
The 'request_token' passed here is the VPS API secret.
"""

import os
import requests
from utils.logging import get_logger

logger = get_logger(__name__)


def _get_executor_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_EXECUTOR_PORT", "5000")
    return "http://%s:%s" % (ip, port)


def authenticate_broker(request_token):
    """
    Authenticate with the MT5 Windows VPS bridge.

    For MT5, 'request_token' is the VPS API secret itself.
    We verify it by hitting the /health endpoint on the VPS.

    Returns:
        (access_token, error_message) - 2-tuple
        access_token is the API secret if connection succeeds, None on failure.
    """
    api_secret = request_token or os.getenv("MT5_API_SECRET", "PARAM_SECRET_2026")
    executor_url = _get_executor_url()

    if not executor_url or "://" not in executor_url:
        return None, "MT5_VPS_IP not configured in environment"

    try:
        headers = {"X-API-Key": api_secret}
        resp = requests.get("%s/health" % executor_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("mt5"):
            logger.info("MT5 VPS authenticated: account=%s", data.get("account"))
            return api_secret, None
        else:
            return None, "MT5 terminal not connected on VPS"

    except requests.exceptions.ConnectionError:
        return None, "Cannot connect to MT5 VPS at %s" % executor_url
    except Exception as e:
        logger.exception("MT5 auth failed")
        return None, "MT5 auth error: %s" % str(e)
