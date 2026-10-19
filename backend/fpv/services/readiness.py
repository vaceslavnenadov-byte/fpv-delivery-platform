"""Готовность к работе: «Готов к работе» / «Не готов к работе» у оператора, координатора и техника.

* Координатор «не готов» — новые заказы ему не назначаются; его заказы, которые ещё не вылетели, сразу
  переходят к готовым координаторам, заказы в полёте остаются за ним (services/coordination.py).
  Стал «готов» — на него переводятся последние поступившие заказы, пока нагрузка не выровняется.
* Оператор «не готов» — предложенные ему, но не принятые заказы переходят к другим готовым операторам
  с нужной квалификацией; нет таких — заказы ждут первого подходящего (services/operator_dispatch.py).
  Стал «готов» — получает ждущие заказы и последние поступившие с выравниванием нагрузки.
* Техник «не готов» — заказы на дронах его парка ведёт готовый техник той же зоны обслуживания
  (services/tech_cover.py); стал «готов» — заказы возвращаются к нему.

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
    if before != ready:
        audit.record(db, p.actor, "work.readiness_changed", entity_type="user", entity_id=p.user_id,
                     details={"role": role, "before": before, "after": ready})
        events.publish(db, "work.readiness", {"user_id": str(p.user_id), "role": role, "ready": ready},
                       users=[p.user_id], roles=["admin"])
        before_n = _count(db, p.user_id, role)
        note = _redistribute(db, role, p.user_id, ready)
        after_n = _count(db, p.user_id, role)
        moved = abs(after_n - before_n)
        out = mine(db, p, role)
        out["moved"] = moved
        out["note"] = note.format(n=moved) if moved else ""
        return out
    out = mine(db, p, role)
    out["moved"], out["note"] = 0, ""
    return out


def _count(db: Session, user_id: uuid.UUID, role: str) -> int:
    """Заказы, которые сейчас за пользователем в этой роли (для сообщения о передаче)."""
    from sqlalchemy import func
    from ..models import Drone, Fleet, Order
    from . import coordination, tech_cover
    if role == "coordinator":
        cond = (Order.coordinator_id == user_id, Order.status.in_(coordination.ACTIVE))
    elif role == "operator":
        cond = (Order.offered_operator_id == user_id, Order.status == "awaiting_operator")
    else:                                    # заказы своего парка, которые ведёт он сам
        return db.execute(select(func.count()).select_from(Order).join(Drone, Drone.id == Order.drone_id)
                          .join(Fleet, Fleet.id == Drone.fleet_id)
                          .where(Fleet.technician_id == user_id, Order.duty_technician_id.is_(None),
                                 Order.status.in_(tech_cover.STATUSES))).scalar_one()
    return db.execute(select(func.count()).select_from(Order).where(*cond)).scalar_one()


def _redistribute(db: Session, role: str, user_id: uuid.UUID, ready: bool) -> str:
    """Сразу перераспределить заказы после смены готовности; шаблон сообщения пользователю."""
    from .. import runtime
    from . import coordination, operator_dispatch, tech_cover
    if role == "coordinator":
        coordination.sweep(db, runtime.settings())
        return "На вас переведено заказов: {n}" if ready else "Передано другим координаторам заказов: {n}"
    if role == "operator":
        if not ready:
            operator_dispatch.release_operator(db, user_id)
        operator_dispatch.sweep(db)
        return "Вам предложено заказов: {n}" if ready else "Передано другим операторам заказов: {n}"
    tech_cover.sweep(db)
    return "Вернулось к вам заказов вашего парка: {n}" if ready else "Заказы вашего парка ведут техники зоны: {n}"
