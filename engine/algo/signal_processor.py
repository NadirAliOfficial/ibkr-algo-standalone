import logging
import math
from datetime import datetime
from typing import Optional
from indicator.signal import Signal, SignalType
from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.algo.state_machine import PositionStateMachine
from engine.algo.earnings_filter import EarningsFilter
from engine.data.polygon import PolygonData

logger = logging.getLogger(__name__)


class SignalProcessor:
    """
    Executes the full state machine on a confirmed flip_long / flip_short signal.

    Flow:
    1. Is ticker active?
    2. Earnings filter — block?
    3. Current position state?
    4. Close existing → confirm close → open opposite
    5. Log all events
    """

    def __init__(
        self,
        store: ConfigStore,
        broker: IBKRConnection,
        state: PositionStateMachine,
        earnings: EarningsFilter,
        polygon: PolygonData,
    ):
        self.store = store
        self.broker = broker
        self.state = state
        self.earnings = earnings
        self.polygon = polygon

    def handle(self, signal: Signal) -> dict:
        """Process a signal. Returns a log dict."""
        ticker = signal.ticker
        log = {
            "timestamp": datetime.utcnow().isoformat(),
            "ticker": ticker,
            "signal": signal.signal_type.value,
            "bar_time": signal.bar_time.isoformat(),
            "close_price": signal.close_price,
            "outcome": "pending",
            "reason": "",
        }

        cfg = self.store.get(ticker)
        if not cfg or not cfg.active:
            log.update(outcome="ignored", reason="ticker inactive")
            logger.info(f"{ticker}: signal ignored — inactive")
            return log

        if self.state.is_halted(ticker):
            log.update(outcome="blocked", reason="halted — unexpected position state")
            return log

        if not self.earnings.check_signal(ticker):
            log.update(outcome="blocked", reason=f"earnings within window")
            return log

        current_state = self.state.get_state(ticker)
        target_state = "long" if signal.signal_type == SignalType.FLIP_LONG else "short"

        if current_state == target_state:
            log.update(outcome="ignored", reason=f"already {target_state}")
            return log

        # Close existing position if not flat
        if current_state != "flat":
            closed = self._close_position(ticker, current_state, cfg)
            if not closed:
                log.update(outcome="error", reason=f"failed to close {current_state}")
                return log

        # Open new position
        opened = self._open_position(ticker, target_state, cfg, signal.close_price)
        if not opened:
            log.update(outcome="error", reason=f"failed to open {target_state}")
            self.state.mark_flat(ticker)
            return log

        if not self.state.transition(ticker, target_state):
            log.update(outcome="error", reason="state transition rejected")
            return log

        log.update(outcome="executed")
        logger.info(f"{ticker}: executed {signal.signal_type.value}")
        return log

    def _close_position(self, ticker: str, current_state: str, cfg) -> bool:
        positions = self.broker.get_positions()
        pos = positions.get(ticker)
        if not pos:
            self.state.mark_flat(ticker)
            return True

        qty = pos["qty"]
        action = "SELL" if current_state == "long" else "BUY"

        self.broker.cancel_all_orders(ticker)
        trade = self.broker.place_market_order(ticker, action, qty)
        if not trade:
            return False

        # Wait for fill confirmation
        for _ in range(20):
            self.broker.sleep(0.5)
            if trade.orderStatus.status in ("Filled", "Inactive"):
                break

        self.state.mark_flat(ticker)
        logger.info(f"{ticker}: closed {current_state} {qty} shares")
        return True

    def _open_position(self, ticker: str, target_state: str, cfg, last_close: float) -> bool:
        price = self.polygon.get_latest_price(ticker) or last_close
        if not price:
            logger.error(f"{ticker}: cannot get price for position sizing")
            return False

        qty = self.broker.calc_shares(cfg.dollar_amount, price)
        if qty <= 0:
            logger.error(f"{ticker}: position size 0 — dollar_amount={cfg.dollar_amount} price={price}")
            return False

        action = "BUY" if target_state == "long" else "SELL"

        if cfg.order_type == "limit":
            bid, ask = self.broker.get_bid_ask(ticker)
            limit_price = ask if action == "BUY" else bid
            if not limit_price:
                limit_price = price
            trade = self.broker.place_limit_order(ticker, action, qty, limit_price)
        else:
            trade = self.broker.place_market_order(ticker, action, qty)

        if not trade:
            return False

        logger.info(f"{ticker}: opened {target_state} {qty}@~{price:.2f}")
        return True
