"""
MT5 Broker Plugin - Order/Position/Trade Data Transformation
===============================================================
Transforms MT5 VPS bridge response data to OpenAlgo standard format.
"""


def map_order_data(order_data):
    """Map raw MT5 order data to OpenAlgo order format."""
    if not order_data:
        return []

    mapped = []
    for order in order_data:
        mapped.append({
            "orderid": order.get("orderid", ""),
            "symbol": order.get("symbol", ""),
            "exchange": "FOREX",
            "action": order.get("action", ""),
            "order_type": order.get("order_type", "MARKET"),
            "quantity": order.get("quantity", "0"),
            "price": order.get("price", "0"),
            "trigger_price": order.get("trigger_price", "0"),
            "product": "NRML",
            "status": order.get("status", "unknown"),
            "timestamp": order.get("timestamp", ""),
        })
    return mapped


def calculate_order_statistics(order_data):
    """Calculate order statistics from order book."""
    stats = {
        "total_buy_orders": 0,
        "total_sell_orders": 0,
        "total_open_orders": 0,
        "total_completed_orders": 0,
        "total_rejected_orders": 0,
    }

    for order in (order_data or []):
        action = order.get("action", "").upper()
        status = order.get("status", "").lower()

        if action == "BUY":
            stats["total_buy_orders"] += 1
        elif action == "SELL":
            stats["total_sell_orders"] += 1

        if status == "open":
            stats["total_open_orders"] += 1
        elif status == "complete":
            stats["total_completed_orders"] += 1
        elif status == "rejected":
            stats["total_rejected_orders"] += 1

    return stats


def transform_order_data(orders):
    """Final transform to standard format."""
    return map_order_data(orders)


def map_trade_data(trade_data):
    """Map raw MT5 trade/deal data to OpenAlgo format."""
    if not trade_data:
        return []

    mapped = []
    for trade in trade_data:
        mapped.append({
            "orderid": trade.get("orderid", ""),
            "tradeid": trade.get("tradeid", ""),
            "symbol": trade.get("symbol", ""),
            "exchange": "FOREX",
            "action": trade.get("action", ""),
            "quantity": trade.get("quantity", "0"),
            "price": trade.get("price", "0"),
            "pnl": trade.get("pnl", "0"),
            "product": "NRML",
            "timestamp": trade.get("timestamp", ""),
        })
    return mapped


def transform_tradebook_data(tradebook_data):
    """Transform trade book data to standard format."""
    return map_trade_data(tradebook_data)


def map_position_data(position_data):
    """Map raw MT5 position data to OpenAlgo format."""
    if not position_data:
        return []

    mapped = []
    for pos in position_data:
        qty = float(pos.get("quantity", 0))
        avg_price = float(pos.get("average_price", 0))
        ltp = float(pos.get("ltp", 0))
        pnl = float(pos.get("pnl", 0))

        mapped.append({
            "symbol": pos.get("tradingsymbol", ""),
            "exchange": pos.get("exchange", "FOREX"),
            "product": pos.get("product", "NRML"),
            "quantity": str(qty),
            "average_price": str(avg_price),
            "ltp": str(ltp),
            "pnl": str(round(pnl, 2)),
        })
    return mapped


def transform_positions_data(positions_data):
    """Transform positions data to standard format."""
    return map_position_data(positions_data)


def transform_holdings_data(holdings_data):
    """No holdings in forex. Return empty list."""
    return []


def map_portfolio_data(portfolio_data):
    """Map portfolio data. For forex, same as positions."""
    return map_position_data(portfolio_data)


def calculate_portfolio_statistics(holdings_data):
    """Calculate portfolio statistics."""
    total_value = 0
    total_pnl = 0

    for h in (holdings_data or []):
        pnl = float(h.get("pnl", 0))
        total_pnl += pnl

    return {
        "total_holdings": len(holdings_data or []),
        "total_pnl": round(total_pnl, 2),
        "total_value": round(total_value, 2),
    }
