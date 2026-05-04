from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import threading

_IST = ZoneInfo("Asia/Kolkata")
_lock = threading.Lock()
_buf: deque = deque(maxlen=200)
_seq = 0


class _Handler(logging.Handler):
    def emit(self, record):
        global _seq
        try:
            with _lock:
                _seq += 1
                _buf.append({
                    "idx": _seq,
                    "ts": datetime.now(_IST).strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "src": record.name.rsplit(".", 1)[-1],
                    "msg": record.getMessage(),
                })
        except Exception:
            pass


def install():
    for name in ("services.delta_neutral_service",):
        lg = logging.getLogger(name)
        if not any(isinstance(h, _Handler) for h in lg.handlers):
            lg.addHandler(_Handler())
            lg.setLevel(logging.DEBUG)


def get_logs(since: int = 0) -> list:
    with _lock:
        return [e for e in _buf if e["idx"] > since]


def current_seq() -> int:
    with _lock:
        return _seq


def clear():
    global _seq
    with _lock:
        _buf.clear()
        _seq = 0
