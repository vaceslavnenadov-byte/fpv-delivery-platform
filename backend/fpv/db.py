"""Подключение к PostgreSQL и управление транзакциями."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def init_engine(url: str, **kwargs) -> Engine:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, pool_pre_ping=True, future=True, **kwargs)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("База данных не инициализирована")
    return _engine


def new_session() -> Session:
    if _factory is None:
        raise RuntimeError("База данных не инициализирована")
    return _factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция: commit при успехе, rollback при исключении."""
    db = new_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
