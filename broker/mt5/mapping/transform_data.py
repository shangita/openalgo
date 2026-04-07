"""
MT5 Broker Plugin - Data Transformation
==========================================
Maps OpenAlgo order/data formats to MT5 VPS bridge formats.
For MT5, symbol names are used as-is (no token lookup needed).
"""


def transform_data(data):
    """
    Transform OpenAlgo order request to MT5 VPS bridge format.

    OpenAlgo sends:
        symbol, exchange, action, quantity, product, pricetype, price, trigger_price

    MT5 VPS expects:
        symbol (tradingsymbol), action (transaction_type), lot, order_type, price
    """
    # MT5 uses symbol names directly (XAUUSD, EURUSD, etc.)
    symbol = data.get("symbol", "")

    return {
        "tradingsymbol": symbol,
        "exchange": data.get("exchange", "FOREX"),
        "transaction_type": data.get("action", "BUY").upper(),
        "order_type": map_order_type(data.get("pricetype", "MARKET")),
        "quantity": data.get("quantity", "0"),
        "product": map_product_type(data.get("product", "NRML")),
        "price": data.get("price", "0"),
        "trigger_price": data.get("trigger_price", "0"),
        "disclosed_quantity": data.get("disclosed_quantity", "0"),
        "validity": "GTC",
        "tag": data.get("tag", "openalgo"),
    }


def transform_modify_order_data(data):
    """Transform OpenAlgo modify order request to MT5 format."""
    return {
        "price": float(data.get("price", 0)),
        "sl": float(data.get("sl", 0)),
        "tp": float(data.get("tp", 0)),
        "order_type": map_order_type(data.get("pricetype", "MARKET")),
        "quantity": data.get("quantity", "0"),
        "trigger_price": float(data.get("trigger_price", 0)),
    }


def map_order_type(pricetype):
    """Map OpenAlgo price type to MT5 order type."""
    mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "SL",
        "SL-M": "SL-M",
        "STOP": "SL",
    }
    return mapping.get(pricetype, "MARKET")


def map_exchange_type(exchange):
    """Map OpenAlgo exchange to MT5 exchange (pass-through for forex)."""
    return exchange


def map_exchange(brexchange):
    """Reverse map: MT5 exchange to OpenAlgo exchange."""
    return brexchange


def map_product_type(product):
    """
    Map OpenAlgo product type to MT5 equivalent.
    Forex uses NRML (normal/margin) for everything.
    """
    mapping = {
        "CNC": "NRML",    # No delivery in forex
        "NRML": "NRML",
        "MIS": "NRML",    # No intraday distinction in forex
    }
    return mapping.get(product, "NRML")


def reverse_map_product_type(product):
    """Reverse map: MT5 product to OpenAlgo product."""
    # Everything in forex is NRML
    return "NRML"
