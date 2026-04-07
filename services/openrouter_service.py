"""
OpenRouter AI Service for PARAM Capital Telegram Bot
Routes natural language trading commands through OpenRouter -> OpenAlgo execution
"""
from __future__ import annotations
import json
import logging
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = "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"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-001"

SYSTEM_PROMPT = (
    "You are an AI assistant for PARAM Capital, an algorithmic trading platform. "
    "You help users control their paper trading via natural language. "
    "Platform uses OpenAlgo with Zerodha (Indian markets) in ANALYZE (paper) mode.\n\n"
    "Respond with ONLY a valid JSON object (no markdown, no explanation):\n"
    '{"intent":"<place_order|cancel_all|positions|funds|pnl|orderbook|tradebook|holdings|quote|status|unknown>",'
    '"reply":"<short friendly reply>",'
    '"params":{"symbol":"","exchange":"","action":"","quantity":0,"product":"MIS","pricetype":"MARKET","price":0}}\n\n'
    "Rules:\n"
    "- Non-order intents (positions,funds,pnl,orderbook,tradebook,holdings,status): leave params as {}\n"
    "- quote intent: only include symbol and exchange in params\n"
    "- Default product=MIS, pricetype=MARKET, price=0\n"
    "- Symbol mappings: NIFTY->NIFTY28APR26FUT NFO, BANKNIFTY->BANKNIFTY23APR26FUT NFO, "
    "HDFCBANK->HDFCBANK28APR26FUT NFO, SILVER->SILVERM30APR26FUT MCX\n"
    "- Keep replies short and clear"
)


def ask_openrouter(user_message: str) -> dict:
    """Send message to OpenRouter, return parsed JSON intent."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://meanrev.duckdns.org",
                    "X-Title": "PARAM Capital",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.1,
                },
            )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {"intent": "unknown", "reply": "Could not parse your request. Try again.", "params": {}}
    except Exception as e:
        logger.exception(f"OpenRouter error: {e}")
        return {"intent": "unknown", "reply": "AI unavailable. Use /help for commands.", "params": {}}


def execute_intent(intent_data: dict, sdk_client) -> str:
    """Execute the parsed intent via OpenAlgo SDK. Returns reply string."""
    intent = intent_data.get("intent", "unknown")
    params = intent_data.get("params", {})
    base_reply = intent_data.get("reply", "")

    try:
        if intent == "place_order":
            qty = int(params.get("quantity", 1))
            action = params.get("action", "BUY")
            result = sdk_client.placesmartorder(
                strategy="AI_BOT",
                symbol=params.get("symbol", ""),
                action=action,
                exchange=params.get("exchange", "NSE"),
                price_type=params.get("pricetype", "MARKET"),
                product=params.get("product", "MIS"),
                quantity=qty,
                position_size=qty if action == "BUY" else -qty,
                price=float(params.get("price", 0)),
            )
            if result and result.get("status") == "success":
                return f"Order placed (Paper)\nOrderID: {result.get('orderid', 'N/A')}\n{base_reply}"
            msg = result.get("message", str(result)) if result else "No response"
            return f"Order failed: {msg}"

        elif intent == "cancel_all":
            sdk_client.cancelallorder(strategy="AI_BOT")
            return "All orders cancelled"

        elif intent == "positions":
            result = sdk_client.positionbook()
            data = result.get("data", []) if result else []
            if not data:
                return "No open positions"
            lines = ["Open Positions", "-------------"]
            for p in data[:10]:
                qty = p.get("quantity", 0)
                side = "LONG" if int(qty) > 0 else "SHORT"
                lines.append(f"{side} {p.get('symbol', '?')} qty={qty} PnL={p.get('pnl', 0)}")
            return "\n".join(lines)

        elif intent == "funds":
            result = sdk_client.funds()
            data = result.get("data", {}) if result else {}
            return f"Funds\nAvailable: Rs {data.get('availablecash', 'N/A')}\nUsed: Rs {data.get('utiliseddebits', 'N/A')}"

        elif intent == "pnl":
            result = sdk_client.funds()
            funds = result.get("data", {}) if result else {}
            try:
                realized = float(funds.get("m2mrealized", 0))
            except (ValueError, TypeError):
                realized = 0.0
            try:
                unrealized = float(funds.get("m2munrealized", 0))
            except (ValueError, TypeError):
                unrealized = 0.0
            total = realized + unrealized
            r_sign = "+" if realized >= 0 else ""
            u_sign = "+" if unrealized >= 0 else ""
            t_sign = "+" if total >= 0 else ""
            return (
                f"PROFIT & LOSS\n"
                f"-------------\n"
                f"Realized:   Rs {r_sign}{realized:,.2f}\n"
                f"Unrealized: Rs {u_sign}{unrealized:,.2f}\n"
                f"Total:      Rs {t_sign}{total:,.2f}"
            )

        elif intent == "orderbook":
            result = sdk_client.orderbook()
            data = result.get("data", []) if result else []
            if not data:
                return "No orders today"
            lines = ["Orders", "------"]
            for o in data[:8]:
                lines.append(f"{o.get('action','?')} {o.get('symbol','?')} {o.get('quantity','?')} - {o.get('order_status','?')}")
            return "\n".join(lines)

        elif intent == "tradebook":
            result = sdk_client.tradebook()
            data = result.get("data", []) if result else []
            if not data:
                return "No trades today"
            lines = ["Trades", "------"]
            for t in data[:8]:
                lines.append(f"{t.get('action','?')} {t.get('symbol','?')} {t.get('quantity','?')} @ {t.get('average_price','?')}")
            return "\n".join(lines)

        elif intent == "holdings":
            result = sdk_client.holdings()
            data = result.get("data", []) if result else []
            if not data:
                return "No holdings"
            lines = ["Holdings", "--------"]
            for h in data[:8]:
                lines.append(f"{h.get('symbol','?')} qty={h.get('quantity','?')} PnL=Rs {h.get('pnl','?')}")
            return "\n".join(lines)

        elif intent == "quote":
            sym = params.get("symbol", "")
            exc = params.get("exchange", "NSE")
            result = sdk_client.quotes(symbol=sym, exchange=exc)
            data = result.get("data", {}) if result else {}
            return f"{sym} ({exc})\nLTP: Rs {data.get('ltp', 'N/A')}"

        elif intent == "status":
            result = sdk_client.funds()
            return "OpenAlgo connected (Paper mode)" if result and result.get("status") == "success" else "OpenAlgo connection failed"

        else:
            return base_reply or "I did not understand. Try:\n- buy 65 nifty futures\n- show positions\n- what is my pnl"

    except Exception as e:
        logger.exception(f"Intent execution error: {e}")
        return f"Execution error: {str(e)[:100]}"
