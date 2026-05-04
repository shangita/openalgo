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


@backtest_bp.route("/backtest/api/datasets", methods=["GET"])
@cross_origin()
@check_session_validity
def backtest_datasets():
    from services.backtest_runner import list_datasets
    return _ok(list_datasets())


@backtest_bp.route("/backtest/api/python-strategies", methods=["GET"])
@cross_origin()
@check_session_validity
def backtest_python_strategies():
    from services.backtest_runner import list_python_strategies
    return _ok(list_python_strategies())


@backtest_bp.route("/backtest/api/run", methods=["POST"])
@cross_origin()
@check_session_validity
def backtest_run():
    body = request.get_json(silent=True) or {}
    dataset_key  = body.get("dataset_key", "").strip()
    strategy_id  = body.get("strategy_id", "").strip()

    if not dataset_key:
        return _err("dataset_key required")
    if not strategy_id:
        return _err("strategy_id required")

    from services.backtest_runner import submit_job
    job_id = submit_job(dataset_key, strategy_id)
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
    return _ok({"logs": get_logs(since), "seq": current_seq()})


@backtest_bp.route("/backtest/logs/clear", methods=["POST"])
@cross_origin()
@check_session_validity
def backtest_clear_logs():
    from services.backtest_log_buffer import clear
    clear()
    return _ok({"message": "Logs cleared"})
