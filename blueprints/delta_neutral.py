"""
Delta Neutral Strategy Blueprint
Monitors open option positions with portfolio Greeks and payoff chart.
"""

import re

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview
from services.delta_neutral_service import get_delta_neutral_portfolio
from services.delta_neutral_log_buffer import install as _install_log
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

delta_neutral_bp = Blueprint("delta_neutral_bp", __name__, url_prefix="/")

_install_log()


@delta_neutral_bp.route("/deltaneutral/api/portfolio", methods=["POST"])
@cross_origin()
@check_session_validity
def portfolio():
    """Get delta neutral portfolio with greeks and payoff data."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify({
                "status": "error",
                "message": "API key not configured. Please generate an API key in /apikey",
            }), 401

        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip()[:20].upper()
        exchange = data.get("exchange", "").strip()[:20].upper()
        expiry_date = data.get("expiry_date", "").strip()[:10].upper()

        if not underlying or not exchange:
            return jsonify({
                "status": "error",
                "message": "underlying and exchange are required",
            }), 400

        if not re.match(r"^[A-Z0-9]+$", underlying):
            return jsonify({"status": "error", "message": "Invalid underlying format"}), 400
        if not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid exchange format"}), 400
        if expiry_date and not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify({
                "status": "error",
                "message": "Invalid expiry_date format. Expected DDMMMYY e.g. 28APR25",
            }), 400

        success, response, status_code = get_delta_neutral_portfolio(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
        )
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Delta neutral portfolio API error: {e}")
        return jsonify({"status": "error", "message": "An error occurred"}), 500


@delta_neutral_bp.route("/deltaneutral/logs", methods=["GET"])
@cross_origin()
@check_session_validity
def delta_neutral_get_logs():
    from services.delta_neutral_log_buffer import get_logs, current_seq
    since = int(request.args.get("since", 0))
    logs = get_logs(since)
    return jsonify({"ok": True, "data": {"logs": logs, "seq": current_seq()}})


@delta_neutral_bp.route("/deltaneutral/logs/clear", methods=["POST"])
@cross_origin()
@check_session_validity
def delta_neutral_clear_logs():
    from services.delta_neutral_log_buffer import clear
    clear()
    return jsonify({"ok": True})
