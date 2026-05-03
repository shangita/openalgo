"""
Scanner models — dataclasses for signals, paper positions, trades, and alerts.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SetupID(str, Enum):
    A = "A"
    B = "B"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    DATA_STALLED = "DATA_STALLED"
    CLOSED = "CLOSED"


class ExitReason(str, Enum):
    TARGET = "TARGET"
    SL = "SL"
    EOD = "EOD"
    DATA_FAIL = "DATA_FAIL"
    MANUAL = "MANUAL"


@dataclass
class ScanResult:
    symbol: str
    exchange: str
    setup_id: SetupID
    direction: Direction
    ltp: float
    ema5: float
    rsi14: float
    target: float
    slope_pct: float             # EMA5 10-bar drift %
    distance_pct: float          # abs(close-ema5)/ema5 %
    pdh: Optional[float]         # Setup B: previous day high
    pdl: Optional[float]         # Setup B: previous day low
    breakout_level: Optional[float]  # Setup B: the level that was crossed
    signal_time: datetime = field(default_factory=datetime.utcnow)
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def dedupe_key(self) -> str:
        return f"{self.signal_time.strftime('%Y-%m-%d')}:{self.symbol}:{self.setup_id.value}"


@dataclass
class Candidate:
    """Setup B daily candidate waiting for breakout trigger."""
    symbol: str
    exchange: str
    ema5: float
    slope_pct: float
    rsi14: float
    pdh: float
    pdl: float
    direction: Direction
    target: float
    refreshed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PaperPosition:
    position_id: str
    signal_id: str
    symbol: str
    exchange: str
    setup_id: SetupID
    direction: Direction
    entry_price: float
    qty: int
    target: float
    stop_loss: float
    trailing_sl: float
    status: PositionStatus
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    pnl: Optional[float] = None
    current_price: float = 0.0
    data_stall_since: Optional[datetime] = None


@dataclass
class AlertRecord:
    dedupe_key: str
    signal_id: str
    sent_at: datetime = field(default_factory=datetime.utcnow)
