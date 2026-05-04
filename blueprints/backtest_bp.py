"""Backtest blueprint — /backtest/* routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin

from utils.session import check_session_validity
from utils.logging import get_logger

logger = get_logger(__name__)

backtest_bp = Blueprint("backtest_bp", __name__, url_prefix="/")


def _ok(data=None, **kwargs):
    resp = {"ok": True, "error": None}
    if data is not None:
        resp["data"] = data
    resp.update(kwargs)
    return jsonify(resp), 200


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg, "data": None}), code


@backtest_bp.route("/backtest/api/strategies", methods=["GET"])
@cross_origin()
@check_session_validity
def backtest_strategies():
    from services.backtest_strategies import STRATEGIES
    data = [
        {
            "id": k,
            "label": v["label"],
            "description": v["description"],
            "default_params": v["default_params"],
        }
        for k, v in STRATEGIES.items()
    ]
    return _ok(data)


@backtest_bp.route("/backtest/api/run", methods=["POST"])
@cross_origin()
@check_session_validity
def backtest_run():
    body = request.get_json(silent=True) or {}
    symbol      = body.get("symbol", "").strip().upper()
    exchange    = body.get("exchange", "NSE").strip().upper()
    interval    = body.get("interval", "D").strip()
    start_date  = body.get("start_date", "")
    end_date    = body.get("end_date", "")
    strategy_id = body.get("strategy", "ema_pullback")
    params      = body.get("params", {})

    if not symbol:
        return _err("symbol required")
    if not start_date or not end_date:
        return _err("start_date and end_date required")

    from services.backtest_runner import submit_job
    try:
        job_id = submit_job(symbol, exchange, interval, start_date, end_date, strategy_id, params)
    except ValueError as exc:
        return _err(str(exc))

    return _ok({"job_id": job_id})


@backtest_bp.route("/backtest/api/status/<job_id>", methods=["GET"])
@cross_origin()
@check_session_validity
def backtest_status(job_id: str):
    from services.backtest_runner import get_job
    job = get_job(job_id)
    if not job:
        return _err("Job not found", 404)
    return _ok(job)


@backtest_bp.route("/backtest/logs", methods=["GET"])
@cross_origin()
@check_session_validity
def backtest_get_logs():
    from services.backtest_log_buffer import current_seq, get_logs
    since = int(request.args.get("since", 0))
    logs = get_logs(since)
    return _ok({"logs": logs, "seq": current_seq()})


@backtest_bp.route("/backtest/logs/clear", methods=["POST"])
@cross_origin()
@check_session_validity
def backtest_clear_logs():
    from services.backtest_log_buffer import clear
    clear()
    return _ok({"message": "Logs cleared"})
