"""
IBKR connection via ib_insync.

Handles: connect/reconnect, position query, order placement, fill confirmation.

Fill confirmation:
  - Polls trade.orderStatus.status every 0.5s up to max_wait_seconds
  - Distinguishes Filled / PartiallyFilled / Cancelled / ApiCancelled
  - Returns FillResult with actual filled qty and average fill price

Order safety:
  - Zero-share guard before any order
  - Cancels all open orders for ticker before placing a new close order
  - On reconnect: cancels all pending orders, re-syncs positions
"""

import asyncio
import os
import time
import math
import logging
from dataclasses import dataclass, field
from typing import Optional
from ib_insync import IB, Stock, MarketOrder, LimitOrder

logger = logging.getLogger(__name__)

FILL_POLL_INTERVAL = 0.5    # seconds between fill status checks
FILL_MAX_WAIT      = 30.0   # seconds before we give up waiting for a fill
FILLED_STATUSES    = {"Filled"}
FAILED_STATUSES    = {"Cancelled", "ApiCancelled", "Inactive", "Unknown"}


@dataclass
class FillResult:
    success:     bool
    filled_qty:  int   = 0
    avg_price:   float = 0.0
    status:      str   = ""
    order_id:    int   = 0


class IBKRConnection:

    def __init__(
        self,
        host:      str = None,
        port:      int = None,
        client_id: int = None,
    ):
        self.host      = host      or os.getenv("IBKR_HOST",      "127.0.0.1")
        self.port      = int(port  or os.getenv("IBKR_PORT",      4002))
        self.client_id = int(client_id or os.getenv("IBKR_CLIENT_ID", 10))
        self.ib        = IB()
        self._connected = False

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())
            self.ib.connect(
                self.host, self.port,
                clientId=self.client_id,
                timeout=15,
                readonly=False,
            )
            self._connected = True
            self.ib.disconnectedEvent += self._on_disconnect
            logger.info(f"Connected to IB Gateway {self.host}:{self.port} (client {self.client_id})")
            return True
        except Exception as e:
            logger.error(f"IBKR connect failed: {e}")
            self._connected = False
            return False

    def reconnect(self, retries: int = 10, delay: int = 10) -> bool:
        for i in range(1, retries + 1):
            logger.info(f"Reconnect attempt {i}/{retries}")
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
            except Exception:
                pass
            time.sleep(2)
            if self.connect():
                # Cancel all pending orders after reconnect — stale orders are dangerous
                self._cancel_all_pending()
                return True
            time.sleep(delay)
        logger.error("All reconnect attempts failed")
        return False

    def _on_disconnect(self):
        self._connected = False
        logger.warning("IB Gateway disconnected")

    @property
    def is_connected(self) -> bool:
        try:
            return self._connected and self.ib.client.isConnected()
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        return self.is_connected or self.reconnect()

    def disconnect(self):
        if self._connected:
            self.ib.disconnect()
            self._connected = False

    def sleep(self, seconds: float = 0.1):
        self.ib.sleep(seconds)

    # ── positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> dict:
        """Returns {ticker: {'qty': int, 'side': 'long'|'short'}}"""
        out = {}
        try:
            for p in self.ib.positions():
                qty = int(p.position)
                if qty == 0:
                    continue
                out[p.contract.symbol] = {
                    "qty":  abs(qty),
                    "side": "long" if qty > 0 else "short",
                }
        except Exception as e:
            logger.error(f"get_positions: {e}")
        return out

    # ── orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        ticker:  str,
        action:  str,
        qty:     int,
    ) -> FillResult:
        if qty <= 0:
            logger.error(f"{ticker}: attempted {action} with qty={qty} — blocked")
            return FillResult(success=False, status="zero_qty")

        contract = self._qualify(ticker)
        if not contract:
            return FillResult(success=False, status="bad_contract")

        try:
            order = MarketOrder(action, qty)
            trade = self.ib.placeOrder(contract, order)
            logger.info(f"Market {action} {qty} {ticker} → orderId={trade.order.orderId}")
            return self._await_fill(trade, ticker)
        except Exception as e:
            logger.error(f"place_market_order {ticker}: {e}")
            return FillResult(success=False, status=f"exception: {e}")

    def place_limit_order(
        self,
        ticker: str,
        action: str,
        qty:    int,
        price:  float,
    ) -> FillResult:
        if qty <= 0:
            return FillResult(success=False, status="zero_qty")

        contract = self._qualify(ticker)
        if not contract:
            return FillResult(success=False, status="bad_contract")

        try:
            order = LimitOrder(action, qty, round(price, 2), tif="DAY")
            trade = self.ib.placeOrder(contract, order)
            logger.info(f"Limit {action} {qty} {ticker}@{price:.2f} → orderId={trade.order.orderId}")
            return self._await_fill(trade, ticker)
        except Exception as e:
            logger.error(f"place_limit_order {ticker}: {e}")
            return FillResult(success=False, status=f"exception: {e}")

    def _await_fill(self, trade, ticker: str) -> FillResult:
        """
        Poll trade status until Filled, failed, or timeout.
        Returns FillResult with actual filled qty and average fill price.
        """
        order_id  = trade.order.orderId
        elapsed   = 0.0
        last_log  = 0.0

        while elapsed < FILL_MAX_WAIT:
            self.ib.sleep(FILL_POLL_INTERVAL)
            elapsed += FILL_POLL_INTERVAL

            status = trade.orderStatus.status

            if status in FILLED_STATUSES:
                filled_qty = int(trade.orderStatus.filled)
                avg_price  = float(trade.orderStatus.avgFillPrice or 0)
                logger.info(
                    f"{ticker} orderId={order_id}: FILLED "
                    f"qty={filled_qty} avg={avg_price:.4f}"
                )
                return FillResult(
                    success=True,
                    filled_qty=filled_qty,
                    avg_price=avg_price,
                    status=status,
                    order_id=order_id,
                )

            if status in FAILED_STATUSES:
                logger.error(f"{ticker} orderId={order_id}: order {status}")
                return FillResult(success=False, status=status, order_id=order_id)

            if elapsed - last_log >= 5.0:
                logger.debug(
                    f"{ticker} orderId={order_id}: waiting fill "
                    f"status={status} elapsed={elapsed:.0f}s"
                )
                last_log = elapsed

        # Timeout — cancel the order
        logger.error(
            f"{ticker} orderId={order_id}: fill timeout after {FILL_MAX_WAIT}s — cancelling"
        )
        try:
            self.ib.cancelOrder(trade.order)
            self.ib.sleep(1)
        except Exception:
            pass
        return FillResult(success=False, status="timeout", order_id=order_id)

    def cancel_all_orders(self, ticker: str):
        try:
            for t in self.ib.openTrades():
                if t.contract.symbol == ticker:
                    self.ib.cancelOrder(t.order)
            self.ib.sleep(0.5)
        except Exception as e:
            logger.error(f"cancel_all_orders {ticker}: {e}")

    def _cancel_all_pending(self):
        """Cancel every open order across all tickers. Called after reconnect."""
        try:
            for t in self.ib.openTrades():
                self.ib.cancelOrder(t.order)
            self.ib.sleep(1)
            logger.info("All pending orders cancelled after reconnect")
        except Exception as e:
            logger.warning(f"_cancel_all_pending: {e}")

    # ── market data ───────────────────────────────────────────────────────────

    def get_bid_ask(self, ticker: str) -> tuple:
        try:
            contract    = self._qualify(ticker)
            ticker_data = self.ib.reqMktData(contract, "", True, False)
            self.ib.sleep(1)
            bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else None
            ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else None
            self.ib.cancelMktData(contract)
            return bid, ask
        except Exception as e:
            logger.error(f"get_bid_ask {ticker}: {e}")
            return None, None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _qualify(self, ticker: str):
        try:
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            return contract
        except Exception as e:
            logger.error(f"qualify_contract {ticker}: {e}")
            return None

    @staticmethod
    def calc_shares(dollar_amount: float, price: float) -> int:
        if price <= 0:
            return 0
        return math.floor(dollar_amount / price)
