"""
Redis-backed log store for signal, trade, and earnings logs.
Falls back to in-memory lists if Redis is unavailable.
"""

import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_MAX = {"signal": 500, "trade": 500, "earnings": 200}
_KEYS = {"signal": "ibkr:signal_log", "trade": "ibkr:trade_log", "earnings": "ibkr:earnings_log"}

_redis = None


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis
        r = redis.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        _redis = r
        logger.info(f"Redis connected: {_REDIS_URL}")
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}) — using in-memory logs")
        _redis = None
    return _redis


class LogStore:
    def __init__(self, kind: str):
        assert kind in _KEYS
        self.kind = kind
        self.key  = _KEYS[kind]
        self.max  = _MAX[kind]
        self._mem: List[dict] = []
        self._load_from_redis()

    def _load_from_redis(self):
        r = _get_redis()
        if not r:
            return
        try:
            raw = r.lrange(self.key, 0, self.max - 1)
            self._mem = [json.loads(x) for x in raw]
        except Exception as e:
            logger.warning(f"LogStore({self.kind}) load failed: {e}")

    def append(self, entry: dict):
        self._mem.append(entry)
        if len(self._mem) > self.max:
            self._mem = self._mem[-self.max:]
        r = _get_redis()
        if r:
            try:
                r.lpush(self.key, json.dumps(entry))
                r.ltrim(self.key, 0, self.max - 1)
            except Exception as e:
                logger.warning(f"LogStore({self.kind}) redis write failed: {e}")

    def recent(self, n: int = 100) -> List[dict]:
        return list(reversed(self._mem[-n:]))

    def __len__(self):
        return len(self._mem)
