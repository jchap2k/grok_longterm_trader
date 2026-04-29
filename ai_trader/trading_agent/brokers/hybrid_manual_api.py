"""
Standalone Hybrid Manual Trading API

- Quotes: Schwab (real-time)
- Orders: Alpaca (paper/live via env)
- FastAPI server with auto /docs
- Easy LLM copy/paste curl commands

Setup:
1. Ensure env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY, SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_REFRESH_TOKEN
2. pip install fastapi uvicorn[standard]  # if not installed
3. uvicorn ai_trader.trading_agent.brokers.hybrid_manual_api:app --host 0.0.0.0 --port 8001 --reload

Usage examples:
$ curl http://localhost:8001/health
$ curl http://localhost:8001/quote/AAPL
$ curl -X POST http://localhost:8001/order -H "Content-Type: application/json" -d '{"symbol":"AAPL","side":"buy","qty":10}'
$ curl -X POST http://localhost:8001/order -H "Content-Type: application/json" -d '{"symbol":"AAPL","side":"buy","qty":10,"type":"limit","limit_price":150.0}'
Docs: http://localhost:8001/docs
"""

import os
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from dataclasses import asdict
import logging
import sys
sys.path.insert(0, '.')

# Broker imports (absolute for standalone run)
from ai_trader.trading_agent.brokers.schwab_broker import SchwabBroker
from ai_trader.trading_agent.brokers.alpaca_broker import AlpacaBroker
from ai_trader.trading_agent.brokers.base_broker import OrderSide, OrderType, Quote, Order, AccountInfo

# Global brokers
schwab: SchwabBroker = None
alpaca: AlpacaBroker = None

app = FastAPI(
    title="Hybrid Manual Trading API",
    description="Schwab quotes + Alpaca orders for manual trading",
    version="1.0.0"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    side: str = Field(..., pattern="^(buy|sell)$")
    qty: int = Field(..., gt=0, le=10000)
    type: str = Field("market", pattern="^(market|limit|stop|moc)$")
    limit_price: Optional[float] = Field(None, ge=0)
    stop_price: Optional[float] = Field(None, ge=0)

class ErrorResponse(BaseModel):
    error: str

@app.on_event("startup")
async def startup_event():
    global schwab, alpaca
    try:
        logger.info("Starting Schwab broker...")
        schwab = SchwabBroker()
        schwab.connect()
        logger.info("Schwab connected")
    except Exception as e:
        logger.warning(f"Schwab unavailable: {e}")
        schwab = None

    try:
        logger.info("Starting Alpaca broker...")
        alpaca = AlpacaBroker(paper_trading=True)
        alpaca.connect()
        logger.info("Alpaca connected")
    except Exception as e:
        logger.warning(f"Alpaca unavailable: {e}")
        alpaca = None

@app.get("/health", response_model=dict)
async def health():
    return {
        "status": "ok",
        "brokers": {
            "schwab_quotes": "connected" if schwab and schwab.connected else "failed",
            "alpaca_orders": "connected" if alpaca and alpaca.connected else "failed"
        },
        "market_open": alpaca.is_market_open() if alpaca else False
    }

@app.get("/quote/{symbol}", response_model=dict)
async def get_quote(symbol: str):
    if not schwab:
        raise HTTPException(503, "Schwab not connected")
    try:
        quote: Quote = schwab.get_quote(symbol.upper())
        quote_dict = asdict(quote)
        quote_dict["timestamp"] = quote.timestamp.isoformat()
        return quote_dict
    except Exception as e:
        logger.error(f"Quote error {symbol}: {e}")
        raise HTTPException(400, str(e))

@app.post("/order", response_model=dict)
async def place_order(req: OrderRequest):
    if not alpaca:
        raise HTTPException(503, "Alpaca not connected")
    try:
        side = OrderSide.BUY if req.side.lower() == "buy" else OrderSide.SELL
        ord_type_map = {
            "market": OrderType.MARKET,
            "limit": OrderType.LIMIT,
            "stop": OrderType.STOP,
            "moc": OrderType.MOC
        }
        ord_type = ord_type_map.get(req.type.lower(), OrderType.MARKET)

        order: Order = alpaca.place_order(
            symbol=req.symbol.upper(),
            side=side,
            quantity=req.qty,
            order_type=ord_type,
            limit_price=req.limit_price,
            stop_price=req.stop_price
        )
        order_dict = asdict(order)
        if order.created_at:
            order_dict["created_at"] = order.created_at.isoformat()
        if order.filled_at:
            order_dict["filled_at"] = order.filled_at.isoformat()
        order_dict["side"] = order.side.value
        order_dict["order_type"] = order.order_type.value
        order_dict["status"] = order.status.value
        return order_dict
    except Exception as e:
        logger.error(f"Order error: {e}")
        raise HTTPException(400, str(e))

@app.get("/account", response_model=dict)
async def get_account():
    if not alpaca:
        raise HTTPException(503, "Alpaca not connected")
    try:
        acc: AccountInfo = alpaca.get_account_info()
        acc_dict = asdict(acc)
        for pos in acc_dict["positions"]:
            pos["unrealized_pnl_percent"] = round(pos["unrealized_pnl_percent"], 2)
        return acc_dict
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/positions", response_model=dict)
async def get_positions():
    if not alpaca:
        raise HTTPException(503, "Alpaca not connected")
    try:
        positions = alpaca.get_positions()
        return [asdict(p) for p in positions]
    except Exception as e:
        raise HTTPException(400, str(e))

if __name__ == "__main__":
    uvicorn.run(
        "hybrid_manual_api:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    )