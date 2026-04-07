"""
MT5 Broker Plugin - Margin Calculator
========================================
Margin calculation for forex positions.
MT5 handles margin server-side, so this is a pass-through.
"""

from utils.logging import get_logger

logger = get_logger(__name__)


def calculate_margin_api(positions, auth, api_key=None):
    """
    Calculate margin for a basket of positions.
    MT5 calculates margin server-side, so we return a simplified estimate.

    Args:
        positions: list of position dicts with symbol, quantity, price
        auth: API secret
        api_key: optional

    Returns:
        (response, response_data) tuple
    """
    total_margin = 0

    for pos in positions:
        qty = float(pos.get("quantity", 0))
        price = float(pos.get("price", 0))
        # Rough forex margin estimate: notional / leverage
        # Default leverage 1:100 for most forex brokers
        leverage = float(pos.get("leverage", 100))
        contract_size = float(pos.get("contract_size", 100000))
        margin = (qty * contract_size * price) / leverage
        total_margin += margin

    response_data = {
        "status": "success",
        "data": {
            "total_margin": round(total_margin, 2),
            "positions": len(positions),
        },
    }

    class MockResponse:
        def __init__(self):
            self.status_code = 200

    return MockResponse(), response_data
