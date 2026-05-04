"""
PARAM Capital strategy registry.
Import strategy modules here so run_all.py can discover them.
"""
from strategies.gold_ema_pullback  import register as _reg_gold
from strategies.silver_rsi_bear    import register as _reg_silver
from strategies.eurusd_dual_osc    import register as _reg_eurusd

REGISTRY: dict = {}

for _fn in (_reg_gold, _reg_silver, _reg_eurusd):
    _meta = _fn()
    REGISTRY[_meta["name"]] = _meta

__all__ = ["REGISTRY"]
