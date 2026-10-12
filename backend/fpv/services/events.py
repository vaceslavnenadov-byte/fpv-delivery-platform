"""Событийная шина: транзакционный outbox + PostgreSQL LISTEN/NOTIFY.

* ``publish`` в одной транзакции с бизнес-изменением записывает событие в
  ``outbox_events`` (для асинхронных обработчиков) и вызывает ``pg_notify``
  (для обновлений в реальном времени через WebSocket). NOTIFY доставляется
  только после COMMIT, поэтому клиенты никогда не увидят событие об
  отменённой транзакции.
* Обработчик outbox выбирает события через ``FOR UPDATE SKIP LOCKED``
  (несколько воркеров), повторяет неудачные попытки с экспоненциальной
  задержкой и ограничением числа попыток (затем статус ``dead``), а
  идемпотентность обеспечивает таблица ``processed_events``.

Отдельный брокер сообщений для первой версии не нужен: PostgreSQL уже
обязателен, а объём событий невелик (см. docs/architecture.md).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import clock
from ..models import OutboxEvent

log = logging.getLogger("fpv.events")
CHANNEL = "fpv_events"

Handler = Callable[[Session, Dict[str, Any], uuid.UUID], None]
_handlers: Dict[str, List[tuple]] = {}


def handler(topic: str, name: Optional[str] = None):
    """Декоратор регистрации обработчика outbox-события."""
    def deco(func: Handler) -> Handler:
        _handlers.setdefault(topic, []).append((name or f"{func.__module__}.{func.__name__}", func))
        return func
    return deco


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def notify(db: Session, kind: str, data: Dict[str, Any], *, users: Iterable[Any] = (),
           roles: Iterable[str] = ()) -> None:
    """Сигнал реального времени. Полезная нагрузка — только идентификаторы;
    клиент запрашивает актуальные данные через REST (сервер — источник истины)."""
    message = {"kind": kind, "data": _jsonable(data),
               "audience": {"users": [str(u) for u in users if u], "roles": list(roles)}}
    payload = json.dumps(message, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > 7900:     # предел pg_notify — 8000 байт
        message["data"] = {k: v for k, v in message["data"].items() if k in ("order_id", "drone_id", "id")}
        payload = json.dumps(message, ensure_ascii=False, default=str)
    db.execute(text("SELECT pg_notify(:ch, :payload)"), {"ch": CHANNEL, "payload": payload})


def publish(db: Session, topic: str, payload: Dict[str, Any], *, users: Iterable[Any] = (),
            roles: Iterable[str] = (), realtime: bool = True, max_attempts: int = 8) -> OutboxEvent:
    users = list(users)
    roles = list(roles)
    event = OutboxEvent(topic=topic, payload=_jsonable(payload), max_attempts=max_attempts,
                        next_attempt_at=clock.now())
    db.add(event)
    db.flush()
    if realtime:
        notify(db, topic, payload, users=users, roles=roles)
    return event


def _backoff(attempts: int) -> dt.timedelta:
    return dt.timedelta(seconds=min(2 ** attempts, 600))


def process_batch(session_factory: Callable[[], Session], limit: int = 50) -> Dict[str, int]:
    """Обрабатывает до ``limit`` готовых событий. Возвращает статистику."""
    stats = {"done": 0, "retry": 0, "dead": 0}
    db = session_factory()
    try:
        now = clock.now()
        rows = list(db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status.in_(("pending", "processing")),
                   OutboxEvent.next_attempt_at <= now,
                   (OutboxEvent.locked_until.is_(None)) | (OutboxEvent.locked_until < now))
            .order_by(OutboxEvent.created_at).limit(limit)
            .with_for_update(skip_locked=True)).scalars())
        for ev in rows:
            ev.status = "processing"
            ev.locked_until = now + dt.timedelta(minutes=5)
        db.commit()
        ids = [ev.id for ev in rows]
    finally:
        db.close()

    for event_id in ids:
        result = _process_one(session_factory, event_id)
        stats[result] += 1
    return stats


def _process_one(session_factory: Callable[[], Session], event_id: uuid.UUID) -> str:
    db = session_factory()
    try:
        ev = db.get(OutboxEvent, event_id)
        if ev is None or ev.status in ("done", "dead"):
            return "done"
        error = None
        for name, func in _handlers.get(ev.topic, []):
            done = db.execute(text("SELECT 1 FROM processed_events WHERE event_id = :e AND handler = :h"),
                              {"e": event_id, "h": name}).first()
            if done:
                continue
            sp = db.begin_nested()
            try:
                func(db, ev.payload, ev.id)
                db.execute(pg_insert(_processed_table()).values(event_id=event_id, handler=name)
                           .on_conflict_do_nothing())
                sp.commit()
            except Exception as exc:  # noqa: BLE001 — любая ошибка обработчика ведёт к повтору
                sp.rollback()
                error = f"{name}: {exc.__class__.__name__}: {exc}"
                log.warning("outbox handler failed: %s\n%s", error, traceback.format_exc(limit=3))
                break
        ev.attempts += 1
        ev.locked_until = None
        if error is None:
            ev.status = "done"
            ev.processed_at = clock.now()
            ev.last_error = None
            result = "done"
        elif ev.attempts >= ev.max_attempts:
            ev.status = "dead"
            ev.last_error = error[:2000]
            result = "dead"
        else:
            ev.status = "pending"
            ev.last_error = error[:2000]
            ev.next_attempt_at = clock.now() + _backoff(ev.attempts)
            result = "retry"
        db.commit()
        return result
    finally:
        db.close()


_processed = None


def _processed_table():
    global _processed
    if _processed is None:
        from sqlalchemy import Column, MetaData, Table, Text
        from sqlalchemy.dialects.postgresql import UUID
        _processed = Table("processed_events", MetaData(), Column("event_id", UUID(as_uuid=True)),
                           Column("handler", Text))
    return _processed


def outbox_stats(db: Session) -> Dict[str, int]:
    rows = db.execute(text("SELECT status, count(*) FROM outbox_events GROUP BY status")).all()
    return {r[0]: int(r[1]) for r in rows}
