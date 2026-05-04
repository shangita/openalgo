"""In-memory log ring buffer for backtest runner."""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import List

_BUFFER_SIZE = 300
_lock = threading.Lock()
_buffer: deque[dict] = deque(maxlen=_BUFFER_SIZE)
_seq = 0


class _BtHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        with _lock:
            _seq += 1
            _buffer.append({
                "seq": _seq,
                "level": record.levelname,
                "msg": self.format(record),
            })


_handler = _BtHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

bt_logger = logging.getLogger("backtest_runner")
bt_logger.addHandler(_handler)
bt_logger.setLevel(logging.DEBUG)
bt_logger.propagate = True


def get_logs(since: int = 0) -> List[dict]:
    with _lock:
        return [e for e in _buffer if e["seq"] > since]


def current_seq() -> int:
    with _lock:
        return _seq


def clear() -> None:
    global _seq
    with _lock:
        _buffer.clear()
        _seq = 0
