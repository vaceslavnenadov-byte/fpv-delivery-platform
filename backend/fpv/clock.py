"""Источник времени. Подменяется в тестах для детерминированных сценариев."""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

_override: Optional[Callable[[], dt.datetime]] = None


def now() -> dt.datetime:
    if _override is not None:
        return _override()
    return dt.datetime.now(dt.timezone.utc)


def set_override(func: Optional[Callable[[], dt.datetime]]) -> None:
    global _override
    _override = func
