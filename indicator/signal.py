from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SignalType(str, Enum):
    FLIP_LONG = "flip_long"
    FLIP_SHORT = "flip_short"


@dataclass
class Signal:
    ticker: str
    signal_type: SignalType
    timestamp: datetime
    close_price: float
    bar_time: datetime
    timeframe: str
