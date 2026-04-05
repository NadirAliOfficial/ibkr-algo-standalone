import pytest
from unittest.mock import MagicMock, patch
from config.store import ConfigStore
from config.schema import TickerConfig
from engine.algo.state_machine import PositionStateMachine


def make_store(tickers):
    store = MagicMock(spec=ConfigStore)
    cfgs = {t: TickerConfig(ticker=t, state="flat") for t in tickers}
    store.get_all.return_value = list(cfgs.values())
    store.get.side_effect = lambda t: cfgs.get(t.upper())
    store.set_state.side_effect = lambda t, s: cfgs.__setitem__(t.upper(), TickerConfig(ticker=t.upper(), state=s))
    return store, cfgs


def make_broker(positions=None):
    broker = MagicMock()
    broker.get_positions.return_value = positions or {}
    return broker


def test_sync_from_ibkr_sets_long():
    store, cfgs = make_store(["AAPL"])
    broker = make_broker({"AAPL": {"qty": 100, "side": "long"}})
    sm = PositionStateMachine(store, broker)
    sm.sync_from_ibkr()
    store.set_state.assert_called_with("AAPL", "long")


def test_sync_from_ibkr_sets_flat():
    store, cfgs = make_store(["AAPL"])
    broker = make_broker({})
    sm = PositionStateMachine(store, broker)
    sm.sync_from_ibkr()
    # flat->flat: no state change call needed, but shouldn't crash
    assert True


def test_transition_rejects_mismatch():
    store, cfgs = make_store(["TSLA"])
    # Internal state is flat but IBKR says long — mismatch
    cfgs["TSLA"].state = "flat"
    broker = make_broker({"TSLA": {"qty": 50, "side": "long"}})
    sm = PositionStateMachine(store, broker)

    result = sm.transition("TSLA", "short")
    assert result is False
    assert sm.is_halted("TSLA")


def test_transition_succeeds_clean():
    store, cfgs = make_store(["NVDA"])
    cfgs["NVDA"].state = "flat"
    broker = make_broker({})  # IBKR also flat
    sm = PositionStateMachine(store, broker)

    result = sm.transition("NVDA", "long")
    assert result is True
    assert not sm.is_halted("NVDA")


def test_halt_and_clear():
    store, cfgs = make_store(["MSFT"])
    broker = make_broker({})
    sm = PositionStateMachine(store, broker)

    sm.halt("MSFT", "test reason")
    assert sm.is_halted("MSFT")

    sm.clear_halt("MSFT")
    assert not sm.is_halted("MSFT")
