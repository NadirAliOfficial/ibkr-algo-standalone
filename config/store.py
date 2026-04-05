import json
import os
import threading
import logging
from typing import Dict, Optional, List
from config.schema import TickerConfig

logger = logging.getLogger(__name__)

CONFIG_FILE = os.getenv("CONFIG_FILE", "config/tickers.json")


class ConfigStore:
    """Thread-safe JSON config store. Redis can replace this later."""

    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, TickerConfig] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                raw = json.load(f)
            self._data = {r["ticker"]: TickerConfig.from_dict(r) for r in raw}
            logger.info(f"Loaded {len(self._data)} tickers from {self.path}")
        else:
            self._data = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump([c.to_dict() for c in self._data.values()], f, indent=2)

    def get_all(self) -> List[TickerConfig]:
        with self._lock:
            return list(self._data.values())

    def get(self, ticker: str) -> Optional[TickerConfig]:
        with self._lock:
            return self._data.get(ticker.upper())

    def upsert(self, cfg: TickerConfig):
        with self._lock:
            self._data[cfg.ticker.upper()] = cfg
            self._save()

    def remove(self, ticker: str):
        with self._lock:
            self._data.pop(ticker.upper(), None)
            self._save()

    def set_state(self, ticker: str, state: str):
        with self._lock:
            cfg = self._data.get(ticker.upper())
            if cfg:
                cfg.state = state
                self._save()

    def set_blocked(self, ticker: str, blocked: bool):
        with self._lock:
            cfg = self._data.get(ticker.upper())
            if cfg:
                cfg.blocked = blocked
                self._save()

    def get_active(self) -> List[TickerConfig]:
        with self._lock:
            return [c for c in self._data.values() if c.active]
