"""
Signal processor — executes the full trade state machine on a confirmed flip signal.

Flow per signal:
  1. Ticker active?
  2. Ticker halted?
  3. Earnings block?
  4. Already in target state? → skip
  5. Close existing position → confirm fill
  6. Open new position → confirm fill
  7. State transition (re-validates against IBKR)
  8. Return detailed log entry

Every order waits for confirmed fill before proceeding to the next step.
A failed close aborts the sequence — we never open a position without knowing the
previous one is closed.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from indicator.signal import Signal, SignalType
from config.store import ConfigStore
from engine.broker.connection import IBKRConnection, FillResult
from engine.algo.state_machine import PositionStateMachine
from engine.algo.earnings_filter import EarningsFilter
from engine.data.polygon import PolygonData

logger = logging.getLogger(__name__)


class SignalProcessor:

    def __init__(
        self,
        store:    ConfigStore,
        broker:   IBKRConnection,
        state:    PositionStateMachine,
        earnings: EarningsFilter,
        polygon:  PolygonData,
    ):
        self.store    = store
        self.broker   = broker
        self.state    = state
        self.earnings = earnings
        self.polygon  = polygon

    def handle(self, signal: Signal) -> dict:
        ticker = signal.ticker
        now    = datetime.now(timezone.utc).isoformat()

        log: dict = {
            "timestamp":    now,
            "ticker":       ticker,
            "signal":       signal.signal_type.value,
            "bar_time":     signal.bar_time.isoformat() if hasattr(signal.bar_time, "isoformat") else str(signal.bar_time),
            "signal_price": signal.close_price,
            "outcome":      "pending",
            "reason":       "",
            "close_fill":   None,
            "open_fill":    None,
            "pnl":          None,
        }

        cfg = self.store.get(ticker)
        if not cfg or not cfg.active:
            return {**log, "outcome": "ignored", "reason": "ticker inactive"}

        if self.state.is_halted(ticker):
            return {**log, "outcome": "blocked", "reason": "halted — awaiting manual resolution"}

        if not self.earnings.check_signal(ticker):
            return {**log, "outcome": "blocked", "reason": "earnings within window"}

        current = self.state.get_state(ticker)
        target  = "long" if signal.signal_type == SignalType.FLIP_LONG else "short"

        if current == target:
            return {**log, "outcome": "ignored", "reason": f"already {target}"}

        # ── close existing position ───────────────────────────────────────────
        close_result: Optional[FillResult] = None
        if current != "flat":
            close_result = self._close_position(ticker, current)
            log["close_fill"] = self._fill_dict(close_result) if close_result else None

            if not close_result or not close_result.success:
                reason = close_result.status if close_result else "no trade returned"
                logger.error(f"{ticker}: failed to close {current} — {reason}")
                # Halt ticker: we don't know the real position state anymore
                self.state.halt(ticker, f"close order failed: {reason}")
                return {**log, "outcome": "error", "reason": f"close failed: {reason}"}

            self.state.mark_flat(ticker)
            logger.info(f"{ticker}: closed {current} — {close_result.filled_qty}@{close_result.avg_price:.4f}")

        # ── open new position ─────────────────────────────────────────────────
        open_result = self._open_position(ticker, target, cfg, signal.close_price)
        log["open_fill"] = self._fill_dict(open_result) if open_result else None

        if not open_result or not open_result.success:
            reason = open_result.status if open_result else "no trade returned"
            logger.error(f"{ticker}: failed to open {target} — {reason}")
            self.state.halt(ticker, f"open order failed: {reason}")
            return {**log, "outcome": "error", "reason": f"open failed: {reason}"}

        # ── state transition ──────────────────────────────────────────────────
        if not self.state.transition(ticker, target):
            # transition() already halted the ticker if IBKR state mismatches
            return {**log, "outcome": "error", "reason": "state transition rejected by IBKR sync"}

        # ── P&L calculation ───────────────────────────────────────────────────
        pnl = None
        if close_result and close_result.success and open_result.success:
            entry = close_result.avg_price
            exit_ = open_result.avg_price
            qty   = close_result.filled_qty
            mult  = 1 if current == "long" else -1
            pnl   = round((exit_ - entry) * qty * mult, 2)

        log.update(outcome="executed", pnl=pnl)
        logger.info(
            f"{ticker}: executed {signal.signal_type.value} "
            f"qty={open_result.filled_qty} fill={open_result.avg_price:.4f}"
            + (f" pnl={pnl}" if pnl is not None else "")
        )
        return log

    # ── close ─────────────────────────────────────────────────────────────────

    def _close_position(self, ticker: str, current_state: str) -> Optional[FillResult]:
        positions = self.broker.get_positions()
        pos = positions.get(ticker)

        if not pos:
            # IBKR says flat — update our state and proceed
            logger.warning(f"{ticker}: expected {current_state} but IBKR shows flat — syncing")
            self.state.mark_flat(ticker)
            return FillResult(success=True, filled_qty=0, avg_price=0.0, status="already_flat")

        qty    = pos["qty"]
        action = "SELL" if current_state == "long" else "BUY"

        self.broker.cancel_all_orders(ticker)
        return self.broker.place_market_order(ticker, action, qty)

    # ── open ──────────────────────────────────────────────────────────────────

    def _open_position(
        self,
        ticker:       str,
        target_state: str,
        cfg,
        last_close:   float,
    ) -> Optional[FillResult]:
        # Get best available price for sizing
        price = self.polygon.get_latest_price(ticker) or last_close
        if not price or price <= 0:
            logger.error(f"{ticker}: cannot determine price — skipping open")
            return FillResult(success=False, status="no_price")

        qty = IBKRConnection.calc_shares(cfg.dollar_amount, price)
        if qty <= 0:
            logger.error(
                f"{ticker}: position size=0 "
                f"(dollar_amount={cfg.dollar_amount}, price={price:.2f}) — skipping"
            )
            return FillResult(success=False, status="zero_qty")

        action = "BUY" if target_state == "long" else "SELL"

        if cfg.order_type == "limit":
            bid, ask = self.broker.get_bid_ask(ticker)
            limit_px = (ask if action == "BUY" else bid) or price
            return self.broker.place_limit_order(ticker, action, qty, limit_px)

        return self.broker.place_market_order(ticker, action, qty)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fill_dict(r: FillResult) -> dict:
        return {
            "success":    r.success,
            "qty":        r.filled_qty,
            "avg_price":  r.avg_price,
            "status":     r.status,
            "order_id":   r.order_id,
        }
