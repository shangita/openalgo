"""
MT5 Broker Plugin - Margin Data Transformation
=================================================
"""


def transform_margin_position(position, client_id=None):
    """Transform a position for margin calculation."""
    if not position:
        return None

    return {
        "symbol": position.get("symbol", ""),
        "exchange": position.get("exchange", "FOREX"),
        "quantity": position.get("quantity", "0"),
        "price": position.get("price", "0"),
        "product": "NRML",
        "action": position.get("action", "BUY"),
    }


def parse_margin_response(response_data):
    """Parse margin response from VPS."""
    if not response_data:
        return {"total_margin": 0}

    data = response_data.get("data", response_data)
    return {
        "total_margin": data.get("total_margin", 0),
    }


def parse_batch_margin_response(responses):
    """Parse batch margin responses."""
    total = 0
    for r in (responses or []):
        parsed = parse_margin_response(r)
        total += parsed.get("total_margin", 0)

    return {"total_margin": round(total, 2)}
