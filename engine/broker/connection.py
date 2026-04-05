import os
import time
import math
import logging
from typing import Optional
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order

logger = logging.getLogger(__name__)


class IBKRConnection:
    """
    ib_insync connection for the algo engine.
    Handles: connect, auto-reconnect, position query, market/limit orders, short-sell.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        client_id: int = None,
    ):
        self.host = host or os.getenv("IBKR_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("IBKR_PORT", 4001))
        self.client_id = int(client_id or os.getenv("IBKR_CLIENT_ID", 10))
        self.ib = IB()
        self._connected = False

    def connect(self) -> bool:
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=15)
            self._connected = True
            self.ib.disconnectedEvent += self._on_disconnect
            logger.info(f"Connected to IB Gateway {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"IBKR connect failed: {e}")
            self._connected = False
            return False

    def reconnect(self, retries: int = 10, delay: int = 10) -> bool:
        for i in range(1, retries + 1):
            logger.info(f"Reconnect attempt {i}/{retries}")
            if self.connect():
                return True
            time.sleep(delay)
        logger.error("All reconnect attempts failed")
        return False

    def _on_disconnect(self):
        self._connected = False
        logger.warning("IB Gateway disconnected — will reconnect")

    @property
    def is_connected(self) -> bool:
        try:
            return self._connected and self.ib.client.isConnected()
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        if not self.is_connected:
            return self.reconnect()
        return True

    def get_positions(self) -> dict:
        """Returns {ticker: {'qty': int, 'side': 'long'|'short'}} from IBKR."""
        positions = {}
        try:
            for p in self.ib.positions():
                symbol = p.contract.symbol
                qty = p.position
                positions[symbol] = {
                    "qty": abs(int(qty)),
                    "side": "long" if qty > 0 else "short",
                }
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
        return positions

    def qualify_contract(self, ticker: str) -> Optional[Stock]:
        try:
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            return contract
        except Exception as e:
            logger.error(f"qualify_contract {ticker}: {e}")
            return None

    def place_market_order(self, ticker: str, action: str, qty: int) -> Optional[object]:
        """action: 'BUY' or 'SELL'"""
        contract = self.qualify_contract(ticker)
        if not contract:
            return None
        try:
            order = MarketOrder(action, qty)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(0.5)
            logger.info(f"Market {action} {qty} {ticker} id={trade.order.orderId}")
            return trade
        except Exception as e:
            logger.error(f"place_market_order {ticker} {action} {qty}: {e}")
            return None

    def place_limit_order(self, ticker: str, action: str, qty: int, price: float) -> Optional[object]:
        contract = self.qualify_contract(ticker)
        if not contract:
            return None
        try:
            order = LimitOrder(action, qty, price, tif="DAY")
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(0.5)
            logger.info(f"Limit {action} {qty} {ticker}@{price} id={trade.order.orderId}")
            return trade
        except Exception as e:
            logger.error(f"place_limit_order {ticker}: {e}")
            return None

    def cancel_all_orders(self, ticker: str):
        try:
            for t in self.ib.openTrades():
                if t.contract.symbol == ticker:
                    self.ib.cancelOrder(t.order)
        except Exception as e:
            logger.error(f"cancel_all_orders {ticker}: {e}")

    def get_bid_ask(self, ticker: str) -> tuple[Optional[float], Optional[float]]:
        try:
            contract = self.qualify_contract(ticker)
            ticker_data = self.ib.reqMktData(contract, "", True, False)
            self.ib.sleep(1)
            bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else None
            ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else None
            self.ib.cancelMktData(contract)
            return bid, ask
        except Exception as e:
            logger.error(f"get_bid_ask {ticker}: {e}")
            return None, None

    def calc_shares(self, dollar_amount: float, price: float) -> int:
        """Whole shares only: floor(dollar_amount / price)"""
        if price <= 0:
            return 0
        return math.floor(dollar_amount / price)

    def sleep(self, seconds: float = 0.1):
        self.ib.sleep(seconds)

    def disconnect(self):
        if self._connected:
            self.ib.disconnect()
            self._connected = False
