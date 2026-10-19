"""Готовность к работе: «Готов к работе» / «Не готов к работе» у оператора, координатора и техника.

* Координатор «не готов» — новые заказы ему не назначаются, их распределяют между готовыми; заказы,
  которые уже у него, остаются за ним. Выравнивание нагрузки — только между готовыми. Стал «готов» —
  на него сразу переводятся последние поступившие заказы, пока нагрузка не выровняется.
* Оператор «не готов» — очередь ему не показывается и принять заказ нельзя; заказы берут другие.
* Техник «не готов» — его парк на паузе: дроны парка не видны заказчику и не принимают новые заказы;
  уже размещённые заказы остаются за ним.

Нет записи в ``work_readiness`` — пользователь готов (так работает и установка до этой версии).
"""
from __future__ import annotations

import uuid
from typing import Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Forbidden
from ..models import WorkReadiness
from . import audit, events
from .identity import Principal

ROLES = ("operator", "coordinator", "technician")
LABELS = {True: "Готов к работе", False: "Не готов к работе"}


def is_ready(db: Session, user_id: uuid.UUID, role: str) -> bool:
    row = db.get(WorkReadiness, (user_id, role))
    return True if row is None else row.ready


def not_ready(db: Session, role: str) -> set:
    """Пользователи роли, отметившие «Не готов к работе»."""
    return set(db.execute(select(WorkReadiness.user_id).where(WorkReadiness.role == role,
                                                              WorkReadiness.ready.is_(False))).scalars())


def only_ready(db: Session, role: str, users: Iterable[uuid.UUID]) -> List[uuid.UUID]:
    off = not_ready(db, role)
    return [u for u in users if u not in off]


def _check_role(p: Principal, role: str) -> None:
    p.require("work.readiness")
    if role not in ROLES or role not in p.roles:
        raise Forbidden("Готовность к работе отмечается только в своей роли", code="readiness_role")


def mine(db: Session, p: Principal, role: str) -> Dict[str, object]:
    _check_role(p, role)
    row = db.get(WorkReadiness, (p.user_id, role))
    ready = True if row is None else row.ready
    return {"role": role, "ready": ready, "label": LABELS[ready],
            "changed_at": row.changed_at.isoformat() if row is not None and row.changed_at else None}


def set_ready(db: Session, p: Principal, role: str, ready: bool) -> Dict[str, object]:
    _check_role(p, role)
    row = db.get(WorkReadiness, (p.user_id, role), with_for_update=True)
    before = True if row is None else row.ready
    if row is None:
        row = WorkReadiness(user_id=p.user_id, role=role, ready=ready, changed_at=clock.now())
        db.add(row)
    else:
        row.ready, row.changed_at = ready, clock.now()
    db.flush()
    moved = 0
    if before != ready:
        audit.record(db, p.actor, "work.readiness_changed", entity_type="user", entity_id=p.user_id,
                     details={"role": role, "before": before, "after": ready})
        events.publish(db, "work.readiness", {"user_id": str(p.user_id), "role": role, "ready": ready},
                       users=[p.user_id], roles=["admin"])
        if role == "coordinator" and ready:
            # Вернулся к работе — на него переводятся последние поступившие заказы (выравнивание).
            from .. import runtime
            from . import coordination
            moved = coordination.sweep(db, runtime.settings())["moved"]
    out = mine(db, p, role)
    out["moved"] = moved
    return out
