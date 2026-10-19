"""Автоматическое назначение координаторов с распределением нагрузки.

Когда заказчик размещает заказ, система назначает координатора, который его согласует и затем
сопровождает, — того, у кого меньше всего заказов в работе и на согласовании (при равенстве —
случайного из них). Предел — ``coordinator_max_orders``
(9: столько трансляций помещается на видеостене координатора). Фоновая задача:

* назначает координаторов заказам, которым их не хватило (все были заняты);
* снимает заказы с координаторов, которые больше не активны (учётная запись отключена, роль снята);
* выравнивает нагрузку: пока разница между самым загруженным и самым свободным координатором —
  2 и больше, один заказ переходит к свободному (сначала не начатые, из них — последние поступившие).

Координатор, отметивший «Не готов к работе» (services/readiness.py), новые заказы не получает и в
выравнивании не участвует; заказы, которые уже у него, остаются за ним.

Администратор по-прежнему может переназначить координатора вручную.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..models import Assignment, Order
from . import audit, events, notifications, readiness

ACTIVE = ("awaiting_coordinator", "awaiting_operator", "accepted", "awaiting_readiness", "ready", "in_progress")
PREFLIGHT = ("awaiting_coordinator", "awaiting_operator", "accepted", "awaiting_readiness", "ready")
MAX_MOVES_PER_RUN = 20

_rng = random.SystemRandom()


def active_coordinators(db: Session) -> List[uuid.UUID]:
    return notifications.users_with_role(db, "coordinator")


def ready_coordinators(db: Session) -> List[uuid.UUID]:
    """Активные координаторы, готовые к работе, — только им назначаются заказы."""
    return readiness.only_ready(db, "coordinator", active_coordinators(db))


def loads(db: Session, coordinators: Optional[List[uuid.UUID]] = None) -> Dict[uuid.UUID, int]:
    coordinators = coordinators if coordinators is not None else ready_coordinators(db)
    counts = Counter(db.execute(select(Order.coordinator_id).where(
        Order.status.in_(ACTIVE), Order.coordinator_id.in_(coordinators))).scalars())
    return {c: counts.get(c, 0) for c in coordinators}


def _pick(load: Dict[uuid.UUID, int], capacity: int, exclude: Optional[uuid.UUID] = None) -> Optional[uuid.UUID]:
    free = {c: n for c, n in load.items() if n < capacity and c != exclude}
    if not free:
        return None
    least = min(free.values())
    return _rng.choice(sorted(c for c, n in free.items() if n == least))


def _set(db: Session, order: Order, coordinator_id: uuid.UUID, reason: str) -> None:
    actor = audit.Actor.system("coordination")
    previous = order.coordinator_id
    for a in db.execute(select(Assignment).where(Assignment.order_id == order.id, Assignment.status == "active",
                                                  Assignment.role == "coordinator")).scalars():
        a.status, a.reason, a.ended_at = "released", reason, clock.now()
    db.flush()
    db.add(Assignment(order_id=order.id, user_id=coordinator_id, role="coordinator", created_by=None,
                      created_at=clock.now()))
    order.coordinator_id = coordinator_id
    order.version += 1
    audit.record(db, actor, "order.coordinator_assigned", entity_type="order", entity_id=order.id,
                 details={"before": previous, "after": coordinator_id, "auto": True, "reason": reason})
    events.publish(db, "order.coordinator_assigned", {"order_id": str(order.id)},
                   users=[coordinator_id, previous, order.operator_id, order.customer_id], roles=["admin"])
    notifications.create(db, [coordinator_id], "order.assigned",
                         f"Заказ №{order.number} ждёт вашего согласования" if order.status == "awaiting_coordinator"
                         else f"Вам назначен заказ №{order.number} для координации", reason, order_id=order.id)
    if previous and previous != coordinator_id:
        notifications.create(db, [previous], "order.assigned",
                             f"Заказ №{order.number} передан другому координатору", reason, order_id=order.id)


def auto_assign(db: Session, settings: Settings, order: Order) -> Optional[uuid.UUID]:
    """Назначить координатора заказу без координатора; None — свободных нет (назначит фоновая задача)."""
    if order.coordinator_id is not None or order.status not in ACTIVE:
        return order.coordinator_id
    chosen = _pick(loads(db), settings.coordinator_max_orders)
    if chosen is not None:
        _set(db, order, chosen, "Автоматическое назначение: наименьшая нагрузка")
    return chosen


def sweep(db: Session, settings: Settings) -> Dict[str, int]:
    """Назначение недостающих, снятие с неактивных, выравнивание нагрузки."""
    stats = {"assigned": 0, "moved": 0}
    active = set(active_coordinators(db))
    coordinators = readiness.only_ready(db, "coordinator", active)
    if not coordinators:
        return stats
    cap = settings.coordinator_max_orders
    orders = list(db.execute(select(Order).where(Order.status.in_(ACTIVE))
                             .order_by(Order.accepted_at.asc().nulls_last(), Order.submitted_at)
                             .with_for_update(of=Order, skip_locked=True)).unique().scalars())
    load = {c: 0 for c in coordinators}
    for o in orders:
        if o.coordinator_id in load:
            load[o.coordinator_id] += 1
    # 1) без координатора или с неактивным координатором (у неготового заказы остаются)
    for o in orders:
        if o.coordinator_id in load or o.coordinator_id in active:
            continue
        chosen = _pick(load, cap)
        if chosen is None:
            break
        _set(db, o, chosen, "Автоматическое назначение: наименьшая нагрузка"
             if o.coordinator_id is None else "Прежний координатор недоступен")
        load[chosen] += 1
        stats["assigned"] += 1
    # 2) выравнивание: разница между самым загруженным и самым свободным — не больше 1
    for _ in range(MAX_MOVES_PER_RUN):
        busiest = max(load, key=lambda c: load[c])
        least = min(load.values())
        if load[busiest] - least < 2:
            break
        mine = [o for o in orders if o.coordinator_id == busiest]
        # сначала не начатые, из них — последние поступившие
        mine.sort(key=lambda o: (o.status not in PREFLIGHT,
                                 -(o.submitted_at or o.created_at).timestamp()))
        target = _pick(load, cap, exclude=busiest)
        if not mine or target is None or load[target] != least:
            break
        _set(db, mine[0], target, "Выравнивание нагрузки координаторов")
        load[busiest] -= 1
        load[target] += 1
        stats["moved"] += 1
    return stats
