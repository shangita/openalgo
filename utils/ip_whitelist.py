"""
SEBI-Compliant IP Whitelisting Middleware for OpenAlgo.

Enforces that API requests (/api/v1/*) only come from whitelisted IPs.
Uses the existing traffic_db infrastructure for storage.

Add to app.py:
    from utils.ip_whitelist import init_ip_whitelist
    init_ip_whitelist(app)
"""

import os
import logging
from functools import wraps

from flask import request, jsonify

logger = logging.getLogger(__name__)

# In-memory whitelist cache (refreshed from DB)
_ip_whitelist_cache = set()
_ip_whitelist_enabled = False


def _load_config():
    """Load IP whitelist config from environment."""
    global _ip_whitelist_enabled
    _ip_whitelist_enabled = os.getenv("SEBI_ENFORCE_IP_WHITELIST", "false").lower() in ("true", "1", "yes")

    # Load static whitelist from env (comma-separated)
    static_ips = os.getenv("SEBI_IP_WHITELIST", "").strip()
    if static_ips:
        for ip in static_ips.split(","):
            ip = ip.strip()
            if ip:
                _ip_whitelist_cache.add(ip)

    # Always allow localhost
    _ip_whitelist_cache.update({"127.0.0.1", "::1", "localhost"})


def add_whitelisted_ip(ip_address):
    """Add an IP to the whitelist."""
    _ip_whitelist_cache.add(ip_address.strip())
    logger.info(f"IP whitelisted: {ip_address}")


def remove_whitelisted_ip(ip_address):
    """Remove an IP from the whitelist."""
    _ip_whitelist_cache.discard(ip_address.strip())
    logger.info(f"IP removed from whitelist: {ip_address}")


def get_whitelisted_ips():
    """Get all whitelisted IPs."""
    return list(_ip_whitelist_cache)


def is_ip_whitelisted(ip_address):
    """Check if an IP is in the whitelist."""
    if not _ip_whitelist_enabled:
        return True
    return ip_address in _ip_whitelist_cache


def _get_client_ip():
    """Get client IP, respecting X-Forwarded-For behind proxy."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def require_whitelisted_ip(f):
    """Decorator to enforce IP whitelist on API endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _ip_whitelist_enabled:
            return f(*args, **kwargs)

        client_ip = _get_client_ip()
        if not is_ip_whitelisted(client_ip):
            logger.warning(f"SEBI IP VIOLATION: Blocked request from {client_ip} to {request.path}")
            return jsonify({
                "status": "error",
                "message": f"SEBI compliance: IP {client_ip} is not whitelisted. "
                           "Register your static IP with your broker.",
            }), 403
        return f(*args, **kwargs)
    return decorated


def init_ip_whitelist(app):
    """
    Initialize IP whitelisting for the Flask app.
    Call this in app.py after creating the Flask app.

    Adds a before_request hook that checks all /api/v1/ requests.
    """
    _load_config()

    if not _ip_whitelist_enabled:
        logger.info("SEBI IP whitelist: DISABLED (set SEBI_ENFORCE_IP_WHITELIST=true to enable)")
        return

    logger.info(f"SEBI IP whitelist: ENABLED with {len(_ip_whitelist_cache)} whitelisted IPs")

    @app.before_request
    def check_ip_whitelist():
        """Check IP whitelist for all API requests."""
        if not request.path.startswith("/api/v1/"):
            return None  # Skip non-API requests

        client_ip = _get_client_ip()
        if not is_ip_whitelisted(client_ip):
            logger.warning(
                f"SEBI IP VIOLATION: Blocked API request from {client_ip} to {request.path}"
            )
            return jsonify({
                "status": "error",
                "message": f"SEBI compliance: IP {client_ip} is not whitelisted for API access.",
            }), 403

        return None
