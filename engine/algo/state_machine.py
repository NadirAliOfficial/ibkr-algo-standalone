import logging
from typing import Optional
from config.store import ConfigStore
from engine.broker.connection import IBKRConnection

logger = logging.getLogger(__name__)

VALID_STATES = {"flat", "long", "short"}


class PositionStateMachine:
    """
    Tracks position state per ticker (flat / long / short).
    Source of truth is always IBKR — internal state is rebuilt on startup and sync'd after fills.
    On unexpected state: halts trading for that ticker and logs anomaly.
    """

    def __init__(self, store: ConfigStore, broker: IBKRConnection):
        self.store = store
        self.broker = broker
        self._halted: set = set()  # tickers with anomalous state — no trading

    def sync_from_ibkr(self):
        """Query all open positions from IBKR and rebuild internal state. Call on startup and reconnect."""
        positions = self.broker.get_positions()
        for cfg in self.store.get_all():
            ticker = cfg.ticker
            ibkr_pos = positions.get(ticker)
            if ibkr_pos is None:
                new_state = "flat"
            elif ibkr_pos["side"] == "long":
                new_state = "long"
            else:
                new_state = "short"

            if cfg.state != new_state:
                logger.info(f"{ticker}: state synced {cfg.state} -> {new_state} from IBKR")
                self.store.set_state(ticker, new_state)

            # Clear halt if IBKR state is clean
            if ticker in self._halted and ibkr_pos is not None:
                logger.info(f"{ticker}: halt cleared after position confirmed")
                self._halted.discard(ticker)

        logger.info("Position sync complete")

    def get_state(self, ticker: str) -> str:
        cfg = self.store.get(ticker)
        return cfg.state if cfg else "flat"

    def is_halted(self, ticker: str) -> bool:
        return ticker in self._halted

    def transition(self, ticker: str, new_state: str) -> bool:
        """
        Attempt state transition. Validates against current IBKR position.
        Returns True if transition is safe to proceed.
        """
        if new_state not in VALID_STATES:
            logger.error(f"{ticker}: invalid target state '{new_state}'")
            return False

        if ticker in self._halted:
            logger.warning(f"{ticker}: trading halted — skipping transition to {new_state}")
            return False

        # Re-query IBKR before every trade
        positions = self.broker.get_positions()
        ibkr_pos = positions.get(ticker)
        current_state = self.get_state(ticker)

        ibkr_state = "flat"
        if ibkr_pos:
            ibkr_state = ibkr_pos["side"]

        # Detect unexpected state
        if current_state != ibkr_state:
            logger.error(
                f"{ticker}: state mismatch — internal={current_state} ibkr={ibkr_state}. "
                f"HALTING ticker until resolved."
            )
            self._halted.add(ticker)
            return False

        self.store.set_state(ticker, new_state)
        logger.info(f"{ticker}: state {current_state} -> {new_state}")
        return True

    def mark_flat(self, ticker: str):
        self.store.set_state(ticker, "flat")

    def halt(self, ticker: str, reason: str):
        logger.error(f"{ticker}: halted — {reason}")
        self._halted.add(ticker)

    def clear_halt(self, ticker: str):
        self._halted.discard(ticker)
        logger.info(f"{ticker}: halt cleared manually")
