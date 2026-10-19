"""Подмена техника парка техником той же зоны обслуживания.

Техник парка отметил «Не готов к работе» (services/readiness.py) — незавершённые заказы на дронах его парка
переходят готовому технику той же зоны обслуживания с наименьшей нагрузкой (``orders.duty_technician_id``).
Замещающему открывается доступ к этим дронам для работы по заказу: подключение, проверка видео,
подтверждение готовности дрона. Техников зоны задаёт администратор («Зоны обслуживания»); у зоны без
назначенных техников подменяют техники, чьи парки стоят в той же зоне.

* Готового техника в зоне нет — заказ остаётся за техником парка, пока кто-нибудь не появится.
* Замещающий сам ушёл со смены — заказ переходит следующему готовому технику зоны.
* Техник парка вернулся («Готов к работе») — заказы его парка возвращаются к нему.
* Нагрузка (заказы, которые техник ведёт) выравнивается между замещающими: пока разница — 2 и больше,
  последний поступивший заказ переходит к менее загруженному технику зоны.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Drone, Fleet, Order, User
from . import audit, events, notifications, readiness, zones

STATUSES = ("awaiting_operator", "accepted", "awaiting_readiness", "ready", "in_progress",
            "awaiting_delivery_confirmation")
MAX_MOVES_PER_RUN = 20

_rng = random.SystemRandom()


def _name(db: Session, user_id: Optional[uuid.UUID]) -> str:
    u = db.get(User, user_id) if user_id else None
    return (u.display_name if u else "") or "—"


def _pool(db: Session, fleet: Fleet, ready: Set[uuid.UUID], cache: Dict[Optional[uuid.UUID], Set[uuid.UUID]]) -> Set[uuid.UUID]:
    """Готовые техники зоны парка, кроме самого техника парка."""
    if fleet.zone_id not in cache:
        assigned = zones._assignments(db, [fleet.zone_id]).get(fleet.zone_id, set()) if fleet.zone_id else set()
        if not assigned:                            # зона открыта всем — техники с парками в этой зоне
            assigned = set(db.execute(select(Fleet.technician_id).where(
                Fleet.active.is_(True), Fleet.zone_id.is_(None) if fleet.zone_id is None
                else Fleet.zone_id == fleet.zone_id)).scalars())
        cache[fleet.zone_id] = assigned
    return {t for t in cache[fleet.zone_id] if t in ready and t != fleet.technician_id}


def _pick(load: Dict[uuid.UUID, int], pool: Set[uuid.UUID]) -> Optional[uuid.UUID]:
    if not pool:
        return None
    least = min(load.get(t, 0) for t in pool)
    return _rng.choice(sorted(t for t in pool if load.get(t, 0) == least))


def _set(db: Session, order: Order, fleet: Fleet, drone: Drone, duty: Optional[uuid.UUID], reason: str) -> None:
    previous = order.duty_technician_id
    if previous == duty:
        return
    order.duty_technician_id = duty
    order.version += 1
    audit.record(db, audit.Actor.system("tech_cover"), "order.duty_technician", entity_type="order",
                 entity_id=order.id, details={"before": previous, "after": duty, "reason": reason})
    events.publish(db, "order.updated", {"order_id": str(order.id)},
                   users=[duty, previous, fleet.technician_id, order.operator_id, order.coordinator_id], roles=["admin"])
    if duty:
        notifications.create(db, [duty], "order.duty",
                             f"Вам передан заказ №{order.number} на дроне {drone.registration_number} "
                             f"(парк «{fleet.name}»)", reason, order_id=order.id)
        notifications.create(db, [fleet.technician_id], "order.duty",
                             f"Заказ №{order.number} на дроне {drone.registration_number} ведёт техник "
                             f"{_name(db, duty)}", reason, order_id=order.id)
    if previous and previous != fleet.technician_id:
        notifications.create(db, [previous], "order.duty",
                             f"Заказ №{order.number} больше не за вами", reason, order_id=order.id)


def serving_technician(order: Order, fleet: Optional[Fleet]) -> Optional[uuid.UUID]:
    """Кто ведёт заказ со стороны парка: замещающий техник или техник парка."""
    return order.duty_technician_id or (fleet.technician_id if fleet else None)


def cover(db: Session, order: Order) -> Optional[uuid.UUID]:
    """Новый заказ на дроне парка, техник которого не готов к работе, — сразу технику зоны."""
    drone = db.get(Drone, order.drone_id)
    fleet = db.get(Fleet, drone.fleet_id) if drone is not None and drone.fleet_id else None
    if fleet is None or order.status not in STATUSES or order.duty_technician_id is not None:
        return order.duty_technician_id
    ready = set(readiness.only_ready(db, "technician", notifications.users_with_role(db, "technician")))
    if fleet.technician_id in ready:
        return None
    pool = _pool(db, fleet, ready, {})
    load = Counter(t for t in db.execute(select(Order.duty_technician_id).where(
        Order.status.in_(STATUSES), Order.duty_technician_id.in_(list(pool)))).scalars())
    owned = Counter(db.execute(select(Fleet.technician_id).join(Drone, Drone.fleet_id == Fleet.id)
                               .join(Order, Order.drone_id == Drone.id)
                               .where(Order.status.in_(STATUSES), Order.duty_technician_id.is_(None),
                                      Fleet.technician_id.in_(list(pool)))).scalars())
    chosen = _pick({t: load.get(t, 0) + owned.get(t, 0) for t in pool}, pool)
    if chosen is not None:
        _set(db, order, fleet, drone, chosen, "Техник парка не готов к работе — заказ передан технику зоны")
    return chosen


def sweep(db: Session) -> Dict[str, int]:
    stats = {"covered": 0, "returned": 0, "moved": 0}
    ready = set(readiness.only_ready(db, "technician", notifications.users_with_role(db, "technician")))
    rows: List[Tuple[Order, Drone, Fleet]] = list(db.execute(
        select(Order, Drone, Fleet).join(Drone, Drone.id == Order.drone_id).join(Fleet, Fleet.id == Drone.fleet_id)
        .where(Order.status.in_(STATUSES)).order_by(Order.submitted_at)
        .with_for_update(of=Order, skip_locked=True)).unique().all())
    load: Counter = Counter(serving_technician(o, f) for o, _, f in rows)
    cache: Dict[Optional[uuid.UUID], Set[uuid.UUID]] = {}
    pools = {o.id: _pool(db, f, ready, cache) for o, _, f in rows}

    def move(o: Order, d: Drone, f: Fleet, to: Optional[uuid.UUID], reason: str) -> None:
        load[serving_technician(o, f)] -= 1
        _set(db, o, f, d, to, reason)
        load[serving_technician(o, f)] += 1

    # 1) техник парка вернулся — заказы к нему; не готов — к готовому технику зоны
    for o, d, f in rows:
        if f.technician_id in ready:
            if o.duty_technician_id is not None:
                move(o, d, f, None, "Техник парка вернулся к работе")
                stats["returned"] += 1
            continue
        if o.duty_technician_id in pools[o.id]:
            continue
        chosen = _pick(load, pools[o.id])
        if chosen is None:
            if o.duty_technician_id is not None:
                move(o, d, f, None, "Готового техника в зоне нет — заказ у техника парка")
            continue
        move(o, d, f, chosen, "Техник парка не готов к работе — заказ передан технику зоны")
        stats["covered"] += 1
    # 2) выравнивание между замещающими: последние поступившие — к менее загруженным
    for _ in range(MAX_MOVES_PER_RUN):
        moved = False
        for o, d, f in sorted((r for r in rows if r[0].duty_technician_id is not None),
                              key=lambda r: -(r[0].submitted_at or r[0].created_at).timestamp()):
            cur = o.duty_technician_id
            pool = {t for t in pools[o.id] if load.get(t, 0) <= load[cur] - 2}
            target = _pick(load, pool)
            if target is not None:
                move(o, d, f, target, "Выравнивание нагрузки техников зоны")
                stats["moved"] += 1
                moved = True
                break
        if not moved:
            break
    return stats
