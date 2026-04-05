import logging
from datetime import datetime
from engine.data.earnings import EarningsChecker
from config.store import ConfigStore
from engine.broker.connection import IBKRConnection
from engine.algo.state_machine import PositionStateMachine
from engine.data.polygon import PolygonData

logger = logging.getLogger(__name__)


class EarningsFilter:
    """
    Two behaviours:
    1. Pre-signal check: block new trades if earnings within window
    2. Daily scan: auto-close existing positions approaching earnings
    """

    def __init__(
        self,
        checker: EarningsChecker,
        store: ConfigStore,
        broker: IBKRConnection,
        state: PositionStateMachine,
        polygon: PolygonData,
        auto_close: bool = True,
    ):
        self.checker = checker
        self.store = store
        self.broker = broker
        self.state = state
        self.polygon = polygon
        self.auto_close = auto_close

    def check_signal(self, ticker: str) -> bool:
        """
        Returns True if safe to trade, False if blocked by earnings.
        """
        has_earnings, date_str = self.checker.has_earnings_soon(ticker)
        if has_earnings:
            logger.warning(f"{ticker}: BLOCKED — earnings on {date_str}")
            self.store.set_blocked(ticker, True)
            return False
        self.store.set_blocked(ticker, False)
        return True

    def daily_scan(self):
        """
        Scan all held positions for upcoming earnings. Auto-close if configured.
        Run once daily via scheduler.
        """
        logger.info("Earnings daily scan started")
        for cfg in self.store.get_active():
            ticker = cfg.ticker
            state = self.state.get_state(ticker)

            has_earnings, date_str = self.checker.has_earnings_soon(ticker)

            if has_earnings:
                self.store.set_blocked(ticker, True)
                logger.warning(f"{ticker}: earnings on {date_str} — blocked")

                if state in ("long", "short") and self.auto_close:
                    self._auto_close(ticker, state, date_str)
            else:
                self.store.set_blocked(ticker, False)

        logger.info("Earnings daily scan complete")

    def _auto_close(self, ticker: str, current_state: str, earnings_date: str):
        """Close existing position ahead of earnings."""
        positions = self.broker.get_positions()
        pos = positions.get(ticker)
        if not pos:
            logger.info(f"{ticker}: no open position to close before earnings")
            self.state.mark_flat(ticker)
            return

        qty = pos["qty"]
        action = "SELL" if current_state == "long" else "BUY"

        logger.info(f"{ticker}: auto-closing {current_state} {qty} shares before earnings {earnings_date}")
        self.broker.cancel_all_orders(ticker)
        trade = self.broker.place_market_order(ticker, action, qty)

        if trade:
            self.state.mark_flat(ticker)
            logger.info(f"{ticker}: position closed before earnings — {action} {qty}")
        else:
            logger.error(f"{ticker}: auto-close FAILED — manual intervention required")
