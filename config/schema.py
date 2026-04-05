from dataclasses import dataclass, field
from typing import Any


@dataclass
class TickerConfig:
    ticker: str
    active: bool = True
    dollar_amount: float = 1000.0
    timeframe: str = "1H"
    indicator_params: dict = field(default_factory=dict)
    order_type: str = "market"   # "market" | "limit"
    state: str = "flat"          # "flat" | "long" | "short"
    blocked: bool = False        # earnings block

    @classmethod
    def from_dict(cls, d: dict) -> "TickerConfig":
        return cls(
            ticker=d["ticker"],
            active=d.get("active", True),
            dollar_amount=float(d.get("dollar_amount", 1000.0)),
            timeframe=d.get("timeframe", "1H"),
            indicator_params=d.get("indicator_params", {}),
            order_type=d.get("order_type", "market"),
            state=d.get("state", "flat"),
            blocked=d.get("blocked", False),
        )

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "active": self.active,
            "dollar_amount": self.dollar_amount,
            "timeframe": self.timeframe,
            "indicator_params": self.indicator_params,
            "order_type": self.order_type,
            "state": self.state,
            "blocked": self.blocked,
        }
