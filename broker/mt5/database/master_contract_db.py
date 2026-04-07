"""
MT5 Broker Plugin - Master Contract Database
================================================
Downloads available symbols from MT5 VPS and populates the SymToken table.
Unlike Indian brokers (daily CSV download), MT5 symbols are relatively static.
"""

import os
import pandas as pd
import requests
from sqlalchemy import func

from extensions import socketio
from utils.logging import get_logger

logger = get_logger(__name__)

# Import shared SymToken model and DB utilities
try:
    from database.token_db import SymToken, db_session
except ImportError:
    # Fallback: define locally if token_db not importable
    from sqlalchemy import Column, String, Float, Integer
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

    class SymToken(Base):
        __tablename__ = "symtoken"
        id = Column(Integer, primary_key=True)
        symbol = Column(String, index=True)
        brsymbol = Column(String, index=True)
        name = Column(String)
        exchange = Column(String)
        brexchange = Column(String)
        token = Column(String)
        expiry = Column(String)
        strike = Column(Float)
        lotsize = Column(Integer)
        instrumenttype = Column(String)
        tick_size = Column(Float)


def _data_url():
    ip = os.getenv("MT5_VPS_IP", "")
    port = os.getenv("MT5_DATA_PORT", "5001")
    return "http://%s:%s" % (ip, port)


def _headers():
    secret = os.getenv("MT5_API_SECRET", "PARAM_SECRET_2026")
    return {"X-API-Key": secret}


def master_contract_download():
    """
    Download MT5 symbol list from VPS and populate SymToken table.

    Emits socketio events for progress tracking (matches OpenAlgo convention).
    """
    try:
        socketio.emit("master_contract_download", {
            "status": "info",
            "message": "Fetching MT5 symbols from VPS..."
        })

        url = "%s/symbols" % _data_url()
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()

        symbols = data.get("symbols", [])
        if not symbols:
            socketio.emit("master_contract_download", {
                "status": "error",
                "message": "No symbols returned from MT5 VPS"
            })
            return

        logger.info("Fetched %d symbols from MT5 VPS", len(symbols))

        # Build DataFrame matching SymToken schema
        rows = []
        for s in symbols:
            symbol_name = s.get("symbol", "")
            exchange = s.get("exchange", "FOREX")

            # Map instrument types
            inst_type = s.get("instrument_type", "FOREX")
            if inst_type in ("FOREX", "COMMODITY"):
                oa_exchange = "FOREX"
            elif inst_type == "CFD":
                oa_exchange = "CFD"
            elif inst_type == "CRYPTO":
                oa_exchange = "CRYPTO"
            elif inst_type == "INDEX":
                oa_exchange = "INDEX"
            else:
                oa_exchange = "FOREX"

            rows.append({
                "symbol": symbol_name,
                "brsymbol": symbol_name,
                "name": s.get("name", symbol_name),
                "exchange": oa_exchange,
                "brexchange": exchange,
                "token": "mt5::::%s" % symbol_name,
                "expiry": "-1",
                "strike": 0.0,
                "lotsize": int(s.get("lot_min", 1) * 100),  # Convert lots to units
                "instrumenttype": inst_type,
                "tick_size": s.get("tick_size", 0.00001),
            })

        df = pd.DataFrame(rows)

        # Delete existing MT5 entries and insert new ones
        with db_session() as session:
            # Remove old MT5 symbols (identified by token prefix)
            session.query(SymToken).filter(
                SymToken.token.like("mt5%")
            ).delete(synchronize_session=False)
            session.commit()

            # Bulk insert new symbols
            for _, row in df.iterrows():
                session.add(SymToken(
                    symbol=row["symbol"],
                    brsymbol=row["brsymbol"],
                    name=row["name"],
                    exchange=row["exchange"],
                    brexchange=row["brexchange"],
                    token=row["token"],
                    expiry=row["expiry"],
                    strike=row["strike"],
                    lotsize=row["lotsize"],
                    instrumenttype=row["instrumenttype"],
                    tick_size=row["tick_size"],
                ))
            session.commit()

        logger.info("MT5 master contract: %d symbols loaded", len(rows))

        socketio.emit("master_contract_download", {
            "status": "success",
            "message": "MT5 symbols loaded: %d instruments" % len(rows)
        })

    except requests.exceptions.ConnectionError:
        msg = "Cannot connect to MT5 VPS at %s" % _data_url()
        logger.error(msg)
        socketio.emit("master_contract_download", {
            "status": "error",
            "message": msg,
        })

    except Exception as e:
        logger.exception("MT5 master contract download failed")
        socketio.emit("master_contract_download", {
            "status": "error",
            "message": "MT5 symbol download failed: %s" % str(e),
        })
