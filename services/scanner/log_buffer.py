from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import threading

_IST = ZoneInfo("Asia/Kolkata")
_lock = threading.Lock()
_buf: deque = deque(maxlen=500)
_seq = 0


class _Handler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
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


_handler = _Handler()
_handler.setLevel(logging.INFO)
_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    parent = logging.getLogger("services.scanner")
    if not any(isinstance(h, _Handler) for h in parent.handlers):
        parent.addHandler(_handler)
    _installed = True


def get_logs(since: int = 0) -> list:
    with _lock:
        return [e for e in _buf if e["idx"] > since]


def current_seq() -> int:
    with _lock:
        return _seq


def clear() -> None:
    global _seq
    with _lock:
        _buf.clear()
        _seq = 0
