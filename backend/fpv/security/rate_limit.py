"""Ограничение частоты запросов (скользящее окно в памяти процесса).

Лимит действует в пределах одного процесса приложения; на периметре
дополнительно работает limit_req в nginx (см. роль Ansible fpv_application).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict

from ..errors import RateLimited


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                raise RateLimited("Слишком много запросов, повторите позже")
            q.append(now)
            if len(self._hits) > 50000:  # защита от неограниченного роста
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
