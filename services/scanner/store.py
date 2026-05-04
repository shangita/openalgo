"""
SQLite persistence for scanner signals, alerts, and paper trades.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

from utils.logging import get_logger

logger = get_logger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "db", "scanner.db")
_DB_URL = "sqlite:///" + os.path.abspath(_DB_PATH)

_engine = create_engine(_DB_URL, connect_args={"check_same_thread": False}, echo=False)
_Session = scoped_session(sessionmaker(bind=_engine))
_init_lock = threading.Lock()
_initialized = False


class Base(DeclarativeBase):
    pass


class SignalRow(Base):
    __tablename__ = "scanner_signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(64), unique=True, index=True)
    symbol = Column(String(32), index=True)
    exchange = Column(String(16))
    setup_id = Column(String(4))
    direction = Column(String(8))
    ltp = Column(Float)
    ema5 = Column(Float)
    rsi14 = Column(Float)
    target = Column(Float)
    slope_pct = Column(Float)
    distance_pct = Column(Float)
    pdh = Column(Float, nullable=True)
    pdl = Column(Float, nullable=True)
    breakout_level = Column(Float, nullable=True)
    signal_time = Column(DateTime, index=True)
    dedupe_key = Column(String(128), unique=True, index=True)


class AlertRow(Base):
    __tablename__ = "scanner_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedupe_key = Column(String(128), unique=True, index=True)
    signal_id = Column(String(64))
    sent_at = Column(DateTime)


class PositionRow(Base):
    __tablename__ = "scanner_positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(String(64), unique=True, index=True)
    signal_id = Column(String(64), index=True)
    symbol = Column(String(32), index=True)
    exchange = Column(String(16))
    setup_id = Column(String(4))
    direction = Column(String(8))
    entry_price = Column(Float)
    qty = Column(Integer)
    target = Column(Float)
    stop_loss = Column(Float)
    trailing_sl = Column(Float)
    status = Column(String(16))
    opened_at = Column(DateTime)
    closed_at = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    exit_reason = Column(String(16), nullable=True)
    pnl = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        os.makedirs(os.path.dirname(os.path.abspath(_DB_PATH)), exist_ok=True)
        Base.metadata.create_all(_engine)
        _initialized = True
        logger.info("Scanner DB initialised at %s", _DB_PATH)


def save_signal(sig) -> None:
    session = _Session()
    try:
        if session.query(SignalRow).filter_by(dedupe_key=sig.dedupe_key).first():
            return
        row = SignalRow(
            signal_id=sig.signal_id, symbol=sig.symbol, exchange=sig.exchange,
            setup_id=sig.setup_id.value, direction=sig.direction.value,
            ltp=sig.ltp, ema5=sig.ema5, rsi14=sig.rsi14, target=sig.target,
            slope_pct=sig.slope_pct, distance_pct=sig.distance_pct,
            pdh=sig.pdh, pdl=sig.pdl, breakout_level=sig.breakout_level,
            signal_time=sig.signal_time, dedupe_key=sig.dedupe_key,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("save_signal error: %s", exc)
    finally:
        session.close()


def get_signals_today() -> list:
    session = _Session()
    try:
        today = datetime.utcnow().date()
        rows = (
            session.query(SignalRow)
            .filter(SignalRow.signal_time >= datetime.combine(today, datetime.min.time()))
            .order_by(SignalRow.signal_time.desc())
            .all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()


def alerts_today() -> set:
    session = _Session()
    try:
        today = datetime.utcnow().date()
        rows = (
            session.query(AlertRow)
            .filter(AlertRow.sent_at >= datetime.combine(today, datetime.min.time()))
            .all()
        )
        return {r.dedupe_key for r in rows}
    finally:
        session.close()


def save_alert(dedupe_key: str, signal_id: str) -> None:
    session = _Session()
    try:
        if session.query(AlertRow).filter_by(dedupe_key=dedupe_key).first():
            return
        session.add(AlertRow(dedupe_key=dedupe_key, signal_id=signal_id, sent_at=datetime.utcnow()))
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("save_alert error: %s", exc)
    finally:
        session.close()


def save_position(pos) -> None:
    session = _Session()
    try:
        row = session.query(PositionRow).filter_by(position_id=pos.position_id).first()
        if row is None:
            row = PositionRow()
            session.add(row)
        row.position_id = pos.position_id
        row.signal_id = pos.signal_id
        row.symbol = pos.symbol
        row.exchange = pos.exchange
        row.setup_id = pos.setup_id.value
        row.direction = pos.direction.value
        row.entry_price = pos.entry_price
        row.qty = pos.qty
        row.target = pos.target
        row.stop_loss = pos.stop_loss
        row.trailing_sl = pos.trailing_sl
        row.status = pos.status.value
        row.opened_at = pos.opened_at
        row.closed_at = pos.closed_at
        row.exit_price = pos.exit_price
        row.exit_reason = pos.exit_reason.value if pos.exit_reason else None
        row.pnl = pos.pnl
        row.current_price = pos.current_price
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("save_position error: %s", exc)
    finally:
        session.close()


update_position = save_position


def get_positions_today() -> list:
    session = _Session()
    try:
        today = datetime.utcnow().date()
        rows = (
            session.query(PositionRow)
            .filter(PositionRow.opened_at >= datetime.combine(today, datetime.min.time()))
            .order_by(PositionRow.opened_at.desc())
            .all()
        )
        return [_pos_row_to_dict(r) for r in rows]
    finally:
        session.close()


def get_open_positions() -> list:
    session = _Session()
    try:
        rows = session.query(PositionRow).filter(
            PositionRow.status.in_(["OPEN", "DATA_STALLED"])
        ).all()
        return [_pos_row_to_dict(r) for r in rows]
    finally:
        session.close()


def get_signal_by_id(signal_id: str) -> Optional[dict]:
    session = _Session()
    try:
        row = session.query(SignalRow).filter_by(signal_id=signal_id).first()
        return _row_to_dict(row) if row else None
    finally:
        session.close()


def _row_to_dict(r: SignalRow) -> dict:
    return {
        "signal_id": r.signal_id, "symbol": r.symbol, "exchange": r.exchange,
        "setup_id": r.setup_id, "direction": r.direction,
        "ltp": r.ltp, "ema5": r.ema5, "rsi14": r.rsi14, "target": r.target,
        "slope_pct": r.slope_pct, "distance_pct": r.distance_pct,
        "pdh": r.pdh, "pdl": r.pdl, "breakout_level": r.breakout_level,
        "signal_time": r.signal_time.isoformat() if r.signal_time else None,
        "dedupe_key": r.dedupe_key,
    }


def _pos_row_to_dict(r: PositionRow) -> dict:
    return {
        "position_id": r.position_id, "signal_id": r.signal_id,
        "symbol": r.symbol, "exchange": r.exchange,
        "setup_id": r.setup_id, "direction": r.direction,
        "entry_price": r.entry_price, "qty": r.qty,
        "target": r.target, "stop_loss": r.stop_loss, "trailing_sl": r.trailing_sl,
        "status": r.status,
        "opened_at": r.opened_at.isoformat() if r.opened_at else None,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "exit_price": r.exit_price, "exit_reason": r.exit_reason,
        "pnl": r.pnl, "current_price": r.current_price,
    }
