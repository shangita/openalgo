"""
MT5 Broker Plugin - Order API
================================
All order operations via the Windows VPS MT5 bridge.
Matches OpenAlgo's expected function signatures exactly.
"""

import os
import requests
from broker.mt5.mapping.transform_data import (
    transform_data,
    transform_modify_order_data,
    map_product_type,
    reverse_map_product_type,
)
from utils.logging import get_logger

logger = get_logger(__name__)


def _executor_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_EXECUTOR_PORT", "5000")
    return "http://%s:%s" % (ip, port)


def _headers(auth):
    return {"X-API-Key": auth, "Content-Type": "application/json"}


def _get(endpoint, auth, timeout=10):
    url = "%s%s" % (_executor_url(), endpoint)
    try:
        r = requests.get(url, headers=_headers(auth), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("MT5 GET %s failed: %s", endpoint, e)
        return {"status": False, "data": None, "error": str(e)}


def _post(endpoint, payload, auth, timeout=10):
    url = "%s%s" % (_executor_url(), endpoint)
    try:
        r = requests.post(url, json=payload, headers=_headers(auth), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("MT5 POST %s failed: %s", endpoint, e)
        return {"status": "error", "message": str(e)}


# -----------------------------------------------------------------
# Order Book
# -----------------------------------------------------------------

def get_order_book(auth):
    """Fetch all orders (pending + today's history)."""
    return _get("/orders", auth)


# -----------------------------------------------------------------
# Trade Book
# -----------------------------------------------------------------

def get_trade_book(auth):
    """Fetch today's executed trades/deals."""
    return _get("/trades", auth)


# -----------------------------------------------------------------
# Positions
# -----------------------------------------------------------------

def get_positions(auth):
    """Fetch open positions in OpenAlgo net format."""
    return _get("/positions/openalgo", auth)


def get_holdings(auth):
    """MT5/Forex has no holdings concept. Return empty."""
    return {"status": True, "data": []}


def get_open_position(tradingsymbol, exchange, product, auth):
    """
    Get net quantity for a specific symbol.
    Returns quantity as string (OpenAlgo convention).
    """
    data = _get("/position?symbol=%s" % tradingsymbol, auth)
    net_qty = data.get("net_qty", 0)

    # Convert lot size to integer-like string for OpenAlgo compatibility
    # MT5 uses lots (0.10) but OpenAlgo expects quantity strings
    return str(net_qty)


# -----------------------------------------------------------------
# Place Order
# -----------------------------------------------------------------

def place_order_api(data, auth):
    """
    Place an order via MT5 VPS bridge.

    Args:
        data: dict with keys: symbol, exchange, action, quantity, product,
              pricetype, price, trigger_price, disclosed_quantity
        auth: API secret string

    Returns:
        (response_obj, response_data, orderid) - 3-tuple matching OpenAlgo convention
    """
    transformed = transform_data(data)

    payload = {
        "symbol": transformed["tradingsymbol"],
        "action": transformed["transaction_type"],
        "lot": float(transformed["quantity"]),
        "order_type": transformed["order_type"],
        "magic": int(data.get("magic", 0)),
        "comment": data.get("tag", "openalgo"),
    }

    # Add price for limit/stop orders
    price = float(transformed.get("price", 0))
    if price > 0 and transformed["order_type"] != "MARKET":
        payload["price"] = price

    # Add SL/TP if provided as absolute prices
    if data.get("sl"):
        payload["sl"] = float(data["sl"])
    if data.get("tp"):
        payload["tp"] = float(data["tp"])

    # Add SL/TP if provided as pips
    if data.get("sl_pips"):
        payload["sl_pips"] = float(data["sl_pips"])
    if data.get("tp_pips"):
        payload["tp_pips"] = float(data["tp_pips"])

    logger.info("MT5 place_order: %s", payload)

    response_data = _post("/order", payload, auth)

    if response_data.get("status") == "success":
        orderid = response_data.get("orderid", response_data.get("data", {}).get("order_id"))
    else:
        orderid = None
        logger.error("MT5 order failed: %s", response_data.get("message", "Unknown"))

    # Create a mock response object with status attribute
    class MockResponse:
        def __init__(self, status_code):
            self.status = status_code
            self.status_code = status_code

    status_code = 200 if orderid else 400
    res = MockResponse(status_code)

    return res, response_data, orderid


def place_smartorder_api(data, auth):
    """
    Smart order: adjusts position to target size.
    Checks current position, calculates delta, places order if needed.
    """
    res = None
    response_data = {"status": "error", "message": "No action required or invalid parameters"}
    orderid = None

    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")

        if not all([symbol, exchange, product]):
            logger.info("Missing required parameters in place_smartorder_api")
            return res, response_data, orderid

        position_size = float(data.get("position_size", "0"))

        # Get current open position
        current_position = float(
            get_open_position(symbol, exchange, map_product_type(product), auth)
        )

        logger.info("SmartOrder: target=%s current=%s for %s",
                     position_size, current_position, symbol)

        action = None
        quantity = 0

        if position_size == 0 and current_position > 0:
            action = "SELL"
            quantity = abs(current_position)
        elif position_size == 0 and current_position < 0:
            action = "BUY"
            quantity = abs(current_position)
        elif current_position == 0:
            action = "BUY" if position_size > 0 else "SELL"
            quantity = abs(position_size)
        else:
            if position_size > current_position:
                action = "BUY"
                quantity = position_size - current_position
            elif position_size < current_position:
                action = "SELL"
                quantity = current_position - position_size

        if action and quantity > 0:
            order_data = data.copy()
            order_data["action"] = action
            order_data["quantity"] = str(quantity)
            res, response_data, orderid = place_order_api(order_data, auth)
            return res, response_data, orderid
        else:
            logger.info("SmartOrder: no action required")
            response_data = {"status": "success", "message": "No action required"}
            return res, response_data, orderid

    except Exception as e:
        error_msg = "Error in place_smartorder_api: %s" % str(e)
        logger.exception(error_msg)
        response_data = {"status": "error", "message": error_msg}
        return res, response_data, orderid


# -----------------------------------------------------------------
# Close All Positions
# -----------------------------------------------------------------

def close_all_positions(current_api_key, auth):
    """Close all open positions."""
    positions_response = get_positions(auth)

    positions_data = positions_response.get("data")
    if positions_data is None:
        return {"message": "No Open Positions Found"}, 200

    net_positions = positions_data.get("net", [])
    if not net_positions:
        return {"message": "No Open Positions Found"}, 200

    for position in net_positions:
        qty = float(position.get("quantity", 0))
        if qty == 0:
            continue

        action = "SELL" if qty > 0 else "BUY"
        abs_qty = abs(qty)

        place_order_payload = {
            "symbol": position["tradingsymbol"],
            "exchange": position.get("exchange", "FOREX"),
            "action": action,
            "quantity": str(abs_qty),
            "product": "NRML",
            "pricetype": "MARKET",
            "price": "0",
            "trigger_price": "0",
            "tag": "squareoff",
        }

        logger.info("Closing position: %s", place_order_payload)
        _, api_response, _ = place_order_api(place_order_payload, auth)
        logger.info("Close response: %s", api_response)

    return {"status": "success", "message": "All Open Positions SquaredOff"}, 200


# -----------------------------------------------------------------
# Cancel Order
# -----------------------------------------------------------------

def cancel_order(orderid, auth):
    """Cancel a pending order by ticket ID."""
    url = "%s/order/%s" % (_executor_url(), orderid)
    try:
        r = requests.delete(url, headers=_headers(auth), timeout=10)
        data = r.json()

        if data.get("status") == "success":
            return {"status": "success", "orderid": orderid}, 200
        else:
            return {"status": "error", "message": data.get("message", "Cancel failed")}, r.status_code

    except Exception as e:
        logger.exception("Cancel order %s failed", orderid)
        return {"status": "error", "message": str(e)}, 500


# -----------------------------------------------------------------
# Modify Order
# -----------------------------------------------------------------

def modify_order(data, auth):
    """Modify a pending order's price/SL/TP."""
    orderid = data.get("orderid")
    transformed = transform_modify_order_data(data)

    url = "%s/order/%s" % (_executor_url(), orderid)
    try:
        r = requests.put(url, json=transformed, headers=_headers(auth), timeout=10)
        resp_data = r.json()

        if resp_data.get("status") == "success":
            return {"status": "success", "orderid": orderid}, 200
        else:
            return {"status": "error", "message": resp_data.get("message", "Modify failed")}, r.status_code

    except Exception as e:
        logger.exception("Modify order %s failed", orderid)
        return {"status": "error", "message": str(e)}, 500


# -----------------------------------------------------------------
# Cancel All Orders
# -----------------------------------------------------------------

def cancel_all_orders_api(data, auth):
    """Cancel all pending orders."""
    order_book = get_order_book(auth)
    orders = order_book.get("data", [])

    canceled = []
    failed = []

    for order in orders:
        if order.get("status") == "open":
            result, status_code = cancel_order(order["orderid"], auth)
            if result.get("status") == "success":
                canceled.append(order["orderid"])
            else:
                failed.append(order["orderid"])

    return canceled, failed
