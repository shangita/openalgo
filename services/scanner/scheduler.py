"""
Continuous scanner scheduler — APScheduler BackgroundScheduler singleton.
Runs Setup A every 30 min, Setup B candidates every 30 min,
Setup B breakout check every 1 min. Auto-stops at 15:30 IST.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from database.market_calendar_db import is_market_holiday
from services.scanner import notifier, store
from services.scanner.data_client import AuthError, clear_daily_cache
from services.scanner.models import SetupID
from services.scanner.universe import get_universe
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_MARKET_OPEN_H, _MARKET_OPEN_M = 9, 15
_MARKET_CLOSE_H, _MARKET_CLOSE_M = 15, 30
_SETUP_A_INTERVAL = 30   # minutes
_SETUP_B_CAND_INTERVAL = 30  # minutes
_SETUP_B_BREAK_INTERVAL = 1  # minutes
_DASHBOARD_URL_TEMPLATE = "{host}/scanner"


class ScannerScheduler:
    _instance: Optional["ScannerScheduler"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._scheduler: Optional[BackgroundScheduler] = None
        self._running = False
        self._paused = False
        self._paused_reason = ""
        self._started_at: Optional[datetime] = None
        self._last_a_run: Optional[str] = None
        self._last_b_run: Optional[str] = None
        self._api_key: Optional[str] = None
        self._host_url = "http://localhost:5000"
        self._b_candidates: list = []
        self._b_candidates_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._initialized = True

    def _now_ist(self) -> datetime:
        return datetime.now(IST)

    def _is_market_open(self, now: datetime) -> bool:
        open_t = now.replace(hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0)
        close_t = now.replace(hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0)
        return open_t <= now <= close_t

    def _dashboard_url(self) -> str:
        return _DASHBOARD_URL_TEMPLATE.format(host=self._host_url)

    # ─── Jobs ─────────────────────────────────────────────────────────────────

    def _job_setup_a(self) -> None:
        now = self._now_ist()
        if not self._is_market_open(now):
            return
        if self._paused:
            return

        from services.scanner.scanner_a import scan

        symbols = get_universe("nifty50")
        try:
            results, auth_err = scan(symbols, self._api_key)
        except Exception as exc:
            logger.error("Setup A job error: %s", exc)
            return

        if auth_err:
            self._handle_auth_error()
            return

        with self._state_lock:
            self._last_a_run = now.strftime("%H:%M IST")
            if self._paused:
                self._resume()

        alerted = store.alerts_today()
        for sig in results:
            if sig.dedupe_key in alerted:
                continue
            store.save_signal(sig)
            store.save_alert(sig.dedupe_key, sig.signal_id)
            msg = notifier.fmt_setup_a(
                sig.symbol, sig.direction.value, sig.ltp, sig.ema5,
                sig.distance_pct, sig.rsi14,
                now.strftime("%Y-%m-%d %H:%M"),
                self._dashboard_url(),
            )
            notifier.send(msg, dedupe_key=sig.dedupe_key)
            logger.info("Setup A alerted: %s %s", sig.symbol, sig.direction.value)

        logger.info("Setup A scan complete: %d signals, %d new", len(results),
                    sum(1 for s in results if s.dedupe_key not in alerted))

    def _job_setup_b_candidates(self) -> None:
        now = self._now_ist()
        if not self._is_market_open(now):
            return
        if self._paused:
            return

        from services.scanner.scanner_b import find_candidates

        symbols = get_universe("nifty50")
        try:
            candidates, auth_err = find_candidates(symbols, self._api_key)
        except Exception as exc:
            logger.error("Setup B candidate job error: %s", exc)
            return

        if auth_err:
            self._handle_auth_error()
            return

        with self._b_candidates_lock:
            self._b_candidates = candidates

        with self._state_lock:
            self._last_b_run = now.strftime("%H:%M IST")
            if self._paused:
                self._resume()

        logger.info("Setup B candidates refreshed: %d", len(candidates))

    def _job_setup_b_breakout(self) -> None:
        now = self._now_ist()
        if not self._is_market_open(now):
            return
        if self._paused:
            return

        from services.scanner.scanner_b import check_breakout

        with self._b_candidates_lock:
            candidates = list(self._b_candidates)

        if not candidates:
            return

        try:
            signals, auth_err = check_breakout(candidates, now, self._api_key)
        except Exception as exc:
            logger.error("Setup B breakout job error: %s", exc)
            return

        if auth_err:
            self._handle_auth_error()
            return

        if self._paused:
            self._resume()

        alerted = store.alerts_today()
        for sig in signals:
            if sig.dedupe_key in alerted:
                continue
            store.save_signal(sig)
            store.save_alert(sig.dedupe_key, sig.signal_id)
            pdh_or_pdl_label = "PDH" if sig.direction.value == "LONG" else "PDL"
            msg = notifier.fmt_setup_b(
                sig.symbol, sig.direction.value, sig.ltp,
                pdh_or_pdl_label, sig.breakout_level or 0,
                sig.ema5, sig.slope_pct, sig.rsi14, sig.target,
                now.strftime("%Y-%m-%d %H:%M"),
                self._dashboard_url(),
            )
            notifier.send(msg, dedupe_key=sig.dedupe_key)

    def _job_auto_stop(self) -> None:
        now = self._now_ist()
        stop_t = now.replace(hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0)
        if now >= stop_t and self._running:
            logger.info("Auto-stopping continuous scanner at %s", now.strftime("%H:%M IST"))
            self.stop()

    def _handle_auth_error(self) -> None:
        with self._state_lock:
            if not self._paused:
                self._paused = True
                self._paused_reason = "Kite auth error"
                notifier.send(notifier.fmt_scanner_paused())
                logger.warning("Scanner paused — Kite auth error")
        clear_daily_cache()

    def _resume(self) -> None:
        if self._paused:
            self._paused = False
            self._paused_reason = ""
            notifier.send(notifier.fmt_scanner_resumed())
            logger.info("Scanner resumed")

    # ─── Public API ────────────────────────────────────────────────────────────

    def start(self, api_key: str, host_url: str = "http://localhost:5000") -> tuple:
        """Start continuous scanner. Returns (ok, error_message)."""
        with self._state_lock:
            if self._running:
                return False, "Scanner already running"

            now = self._now_ist()
            today = now.date()

            # Block on non-trading days
            if is_market_holiday(today, "NSE"):
                return False, f"{today} is a market holiday or weekend — scanner not started"

            self._api_key = api_key
            self._host_url = host_url
            self._running = True
            self._paused = False
            self._paused_reason = ""
            self._started_at = now
            self._b_candidates = []

        self._scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60}
        )

        # Setup A: every 30 min
        self._scheduler.add_job(
            self._job_setup_a, "interval", minutes=_SETUP_A_INTERVAL,
            id="scanner_a", replace_existing=True
        )
        # Setup B candidates: every 30 min
        self._scheduler.add_job(
            self._job_setup_b_candidates, "interval", minutes=_SETUP_B_CAND_INTERVAL,
            id="scanner_b_cand", replace_existing=True
        )
        # Setup B breakout: every 1 min
        self._scheduler.add_job(
            self._job_setup_b_breakout, "interval", minutes=_SETUP_B_BREAK_INTERVAL,
            id="scanner_b_break", replace_existing=True
        )
        # Auto-stop check: every minute
        self._scheduler.add_job(
            self._job_auto_stop, "interval", minutes=1,
            id="scanner_auto_stop", replace_existing=True
        )

        self._scheduler.start()

        # Run immediately on start
        threading.Thread(target=self._job_setup_a, daemon=True, name="scanner-a-init").start()
        threading.Thread(target=self._job_setup_b_candidates, daemon=True, name="scanner-b-init").start()

        ts = now.strftime("%H:%M")
        notifier.send(notifier.fmt_scanner_started(ts))
        logger.info("Continuous scanner started at %s IST", ts)
        return True, None

    def stop(self) -> None:
        with self._state_lock:
            if not self._running:
                return
            self._running = False

        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as exc:
                logger.warning("Scheduler shutdown error: %s", exc)
            self._scheduler = None

        ts = self._now_ist().strftime("%H:%M")
        notifier.send(notifier.fmt_scanner_stopped(ts))
        logger.info("Continuous scanner stopped at %s IST", ts)

    def status(self) -> dict:
        with self._state_lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "paused_reason": self._paused_reason,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "last_a_run": self._last_a_run,
                "last_b_run": self._last_b_run,
                "b_candidates": len(self._b_candidates),
            }


def get_scheduler() -> ScannerScheduler:
    return ScannerScheduler()
