"""Audit Service: неизменяемый журнал аудита с хеш-цепочкой.

Каждая запись содержит хеш предыдущей; изменение или удаление любой записи
обнаруживается функцией verify_chain. Дополнительно UPDATE/DELETE/TRUNCATE
запрещены триггерами на уровне БД (миграция 0001).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import clock
from ..models import AuditLog

GENESIS = "0" * 64
_SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "api_key", "authorization", "cookie"}


@dataclass
class Actor:
    """Кто выполняет действие: пользователь, система или дрон."""
    user_id: Optional[uuid.UUID]
    label: str
    ip: Optional[str] = None

    @classmethod
    def system(cls, label: str = "system") -> "Actor":
        return cls(None, label)


def _scrub(value: Any) -> Any:
    """Удаляет из деталей секреты: пароли и токены никогда не пишутся в журнал."""
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in _SENSITIVE_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _ts(value) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def _digest(prev_hash: str, occurred_at: str, actor_id: Optional[str], actor_label: str, action: str,
            entity_type: str, entity_id: str, outcome: str, details: Dict[str, Any]) -> str:
    canonical = json.dumps(
        [prev_hash, occurred_at, actor_id, actor_label, action, entity_type, entity_id, outcome, details],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record(db: Session, actor: Actor, action: str, *, entity_type: str = "", entity_id: Any = "",
           outcome: str = "success", details: Optional[Dict[str, Any]] = None) -> AuditLog:
    """Добавляет запись в журнал в рамках текущей транзакции."""
    # Сериализация вставок для корректной цепочки хешей (блокировка до конца транзакции).
    db.execute(text("SELECT pg_advisory_xact_lock(7243002)"))
    last = db.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
    prev_hash = last or GENESIS
    occurred_at = clock.now()
    clean = _scrub(details or {})
    entity_id_s = str(entity_id) if entity_id is not None else ""
    actor_id_s = str(actor.user_id) if actor.user_id else None
    digest = _digest(prev_hash, _ts(occurred_at), actor_id_s, actor.label, action,
                     entity_type, entity_id_s, outcome, clean)
    entry = AuditLog(occurred_at=occurred_at, actor_id=actor.user_id, actor_label=actor.label,
                     actor_ip=actor.ip, action=action, entity_type=entity_type, entity_id=entity_id_s,
                     outcome=outcome, details=clean, prev_hash=prev_hash, hash=digest)
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, batch: int = 5000) -> Dict[str, Any]:
    """Проверяет целостность цепочки. Возвращает {'ok': bool, 'checked': n, 'broken_at': id|None}."""
    prev = GENESIS
    checked = 0
    last_id = 0
    while True:
        rows: List[AuditLog] = list(db.execute(
            select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch)).scalars())
        if not rows:
            break
        for row in rows:
            expected = _digest(prev, _ts(row.occurred_at), str(row.actor_id) if row.actor_id else None,
                               row.actor_label, row.action, row.entity_type, row.entity_id, row.outcome,
                               row.details)
            if row.prev_hash != prev or row.hash != expected:
                return {"ok": False, "checked": checked, "broken_at": row.id}
            prev = row.hash
            checked += 1
            last_id = row.id
    return {"ok": True, "checked": checked, "broken_at": None}
