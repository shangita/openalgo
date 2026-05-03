"""
Paper trading engine — intraday simulation with trailing stop loss.
Runs independently from the scanner scheduler.
"""
from __future__ import annotations

import math
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

import pytz

from services.scanner.data_client import DataFetchError, AuthError, get_intraday_bars
from services.scanner.indicators import atr, swing_high, swing_low
from services.scanner.models import (
    Direction, ExitReason, PaperPosition, PositionStatus, SetupID,
)
from services.scanner import store
from services.scanner import notifier
from utils.logging import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ─── Config defaults ───────────────────────────────────────────────────────────
_NOTIONAL = 100_000.0
_INITIAL_SL_MULT = 1.5
_TRAIL_SL_MULT = 1.0
_SQUAREOFF_H, _SQUAREOFF_M = 15, 15
_MARKET_OPEN_H, _MARKET_OPEN_M = 9, 15
_MARKET_CLOSE_H, _MARKET_CLOSE_M = 15, 30
_DATA_STALL_RETRY = 30
_DATA_STALL_FORCE_EXIT_MIN = 5
_INTRADAY_INTERVAL = "5m"
_INTRADAY_LOOKBACK = 5
_ATR_PERIOD = 14

# ─── State ─────────────────────────────────────────────────────────────────────
_positions: dict[str, PaperPosition] = {}  # position_id -> PaperPosition
_positions_lock = threading.Lock()
_engine_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_api_key: Optional[str] = None


def _now_ist() -> datetime:
    return datetime.now(IST)


def open_position(
    signal_id: str,
    symbol: str,
    exchange: str,
    setup_id: SetupID,
    direction: Direction,
    target: float,
    api_key: str,
    notional: float = _NOTIONAL,
    initial_sl_mult: float = _INITIAL_SL_MULT,
) -> Optional[PaperPosition]:
    """
    Open a paper position. Fetches current 5-min bars, computes entry/SL.
    Returns the new PaperPosition, or None if data unavailable.
    """
    try:
        df5 = get_intraday_bars(symbol, exchange, _INTRADAY_INTERVAL, _INTRADAY_LOOKBACK, api_key)
    except (DataFetchError, AuthError) as exc:
        logger.error("Cannot open paper position for %s: %s", symbol, exc)
        return None

    if df5.empty or len(df5) < _ATR_PERIOD + 2:
        logger.warning("Insufficient bars to open paper position for %s", symbol)
        return None

    entry_price = float(df5["close"].iloc[-1])
    qty = max(1, math.floor(notional / entry_price))

    atr_vals = atr(df5["high"], df5["low"], df5["close"], _ATR_PERIOD)
    current_atr = float(atr_vals.iloc[-1])

    if direction == Direction.LONG:
        swing_sl = swing_low(df5["low"], window=5)
        atr_sl = entry_price - initial_sl_mult * current_atr
        stop_loss = min(atr_sl, swing_sl)
    else:
        swing_sl = swing_high(df5["high"], window=5)
        atr_sl = entry_price + initial_sl_mult * current_atr
        stop_loss = max(atr_sl, swing_sl)

    trailing_sl = stop_loss
    pos_id = str(uuid.uuid4())

    pos = PaperPosition(
        position_id=pos_id,
        signal_id=signal_id,
        symbol=symbol,
        exchange=exchange,
        setup_id=setup_id,
        direction=direction,
        entry_price=entry_price,
        qty=qty,
        target=target,
        stop_loss=stop_loss,
        trailing_sl=trailing_sl,
        status=PositionStatus.OPEN,
        current_price=entry_price,
    )

    with _positions_lock:
        _positions[pos_id] = pos

    store.save_position(pos)
    notifier.send(notifier.fmt_paper_open(symbol, direction.value, entry_price, stop_loss, target))
    logger.info("Paper position opened: %s %s entry=%.2f sl=%.2f tgt=%.2f qty=%d",
                symbol, direction.value, entry_price, stop_loss, target, qty)
    return pos


def close_position(
    position_id: str,
    exit_price: float,
    reason: ExitReason,
) -> Optional[PaperPosition]:
    with _positions_lock:
        pos = _positions.get(position_id)
        if pos is None or pos.status == PositionStatus.CLOSED:
            return None
        pos.status = PositionStatus.CLOSED
        pos.exit_price = exit_price
        pos.exit_reason = reason
        pos.closed_at = datetime.utcnow()
        if pos.direction == Direction.LONG:
            pos.pnl = (exit_price - pos.entry_price) * pos.qty
        else:
            pos.pnl = (pos.entry_price - exit_price) * pos.qty

    store.update_position(pos)

    if reason == ExitReason.TARGET:
        notifier.send(notifier.fmt_paper_target(pos.symbol, exit_price, pos.pnl))
    elif reason == ExitReason.SL:
        notifier.send(notifier.fmt_paper_sl(pos.symbol, exit_price, pos.pnl))
    elif reason == ExitReason.EOD:
        notifier.send(notifier.fmt_paper_eod(pos.symbol, exit_price, pos.pnl))
    elif reason == ExitReason.DATA_FAIL:
        notifier.send(notifier.fmt_paper_data_fail(pos.symbol))

    logger.info("Paper position closed: %s %s exit=%.2f reason=%s pnl=%.0f",
                pos.symbol, pos.direction.value, exit_price, reason.value, pos.pnl)
    return pos


def _update_one(pos: PaperPosition, api_key: str, trail_mult: float) -> None:
    """Update trailing SL and check exit for one open position."""
    now_ist = _now_ist()

    # EOD square-off
    sq_time = now_ist.replace(hour=_SQUAREOFF_H, minute=_SQUAREOFF_M, second=0, microsecond=0)
    if now_ist >= sq_time and pos.status == PositionStatus.OPEN:
        current_price = pos.current_price or pos.entry_price
        close_position(pos.position_id, current_price, ExitReason.EOD)
        return

    # Fetch latest bar
    try:
        df5 = get_intraday_bars(pos.symbol, pos.exchange, _INTRADAY_INTERVAL, 3, api_key)
        if pos.status == PositionStatus.DATA_STALLED:
            with _positions_lock:
                pos.status = PositionStatus.OPEN
                pos.data_stall_since = None
            store.update_position(pos)
            notifier.send(f"Data recovered for {pos.symbol}", dedupe_key=None)
    except AuthError as exc:
        logger.warning("Auth error updating position %s: %s", pos.symbol, exc)
        now = datetime.utcnow()
        with _positions_lock:
            if pos.status != PositionStatus.DATA_STALLED:
                pos.status = PositionStatus.DATA_STALLED
                pos.data_stall_since = now
                store.update_position(pos)
            elif (now - pos.data_stall_since).total_seconds() > _DATA_STALL_FORCE_EXIT_MIN * 60:
                close_position(pos.position_id, pos.current_price or pos.entry_price, ExitReason.DATA_FAIL)
        return
    except DataFetchError as exc:
        logger.warning("Data error updating %s: %s", pos.symbol, exc)
        return

    if df5.empty or len(df5) < 2:
        return

    last_bar = df5.iloc[-2]
    bar_close = float(last_bar["close"])

    with _positions_lock:
        pos.current_price = bar_close

    atr_vals = atr(df5["high"], df5["low"], df5["close"], min(_ATR_PERIOD, len(df5)))
    curr_atr = float(atr_vals.iloc[-1])

    # Update trailing SL (ratchet only)
    with _positions_lock:
        if pos.direction == Direction.LONG:
            new_sl = bar_close - trail_mult * curr_atr
            pos.trailing_sl = max(pos.trailing_sl, new_sl)
        else:
            new_sl = bar_close + trail_mult * curr_atr
            pos.trailing_sl = min(pos.trailing_sl, new_sl)
        trailing_sl = pos.trailing_sl

    # Check target hit
    if pos.direction == Direction.LONG and bar_close >= pos.target:
        close_position(pos.position_id, bar_close, ExitReason.TARGET)
        return
    if pos.direction == Direction.SHORT and bar_close <= pos.target:
        close_position(pos.position_id, bar_close, ExitReason.TARGET)
        return

    # Check trailing SL hit
    if pos.direction == Direction.LONG and bar_close <= trailing_sl:
        close_position(pos.position_id, bar_close, ExitReason.SL)
        return
    if pos.direction == Direction.SHORT and bar_close >= trailing_sl:
        close_position(pos.position_id, bar_close, ExitReason.SL)
        return

    store.update_position(pos)


def _engine_loop(api_key: str, trail_mult: float) -> None:
    logger.info("Paper engine started")
    while not _stop_event.is_set():
        now_ist = _now_ist()
        open_h = now_ist.replace(hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0)
        close_h = now_ist.replace(hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0)

        if open_h <= now_ist <= close_h:
            with _positions_lock:
                active = [p for p in _positions.values() if p.status != PositionStatus.CLOSED]
            for pos in active:
                try:
                    _update_one(pos, api_key, trail_mult)
                except Exception as exc:
                    logger.error("Paper engine error for %s: %s", pos.symbol, exc)

        _stop_event.wait(timeout=60)

    logger.info("Paper engine stopped")


def start_engine(api_key: str, trail_mult: float = _TRAIL_SL_MULT) -> None:
    global _engine_thread, _api_key
    _api_key = api_key
    _stop_event.clear()

    # Reload open positions from DB
    open_rows = store.get_open_positions()
    with _positions_lock:
        for row in open_rows:
            if row["position_id"] not in _positions:
                from services.scanner.models import SetupID, Direction, PositionStatus
                pos = PaperPosition(
                    position_id=row["position_id"],
                    signal_id=row["signal_id"],
                    symbol=row["symbol"],
                    exchange=row["exchange"],
                    setup_id=SetupID(row["setup_id"]),
                    direction=Direction(row["direction"]),
                    entry_price=row["entry_price"],
                    qty=row["qty"],
                    target=row["target"],
                    stop_loss=row["stop_loss"],
                    trailing_sl=row["trailing_sl"],
                    status=PositionStatus(row["status"]),
                    current_price=row.get("current_price") or row["entry_price"],
                )
                _positions[pos.position_id] = pos

    if _engine_thread and _engine_thread.is_alive():
        return  # already running

    _engine_thread = threading.Thread(
        target=_engine_loop, args=(api_key, trail_mult), daemon=True, name="paper-engine"
    )
    _engine_thread.start()


def stop_engine() -> None:
    _stop_event.set()


def get_positions() -> list[dict]:
    return store.get_positions_today()


def manual_close(position_id: str) -> bool:
    with _positions_lock:
        pos = _positions.get(position_id)
    if not pos:
        return False
    close_position(position_id, pos.current_price or pos.entry_price, ExitReason.MANUAL)
    return True
