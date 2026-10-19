"""Автоматическое назначение операторов с распределением нагрузки.

Заказ, который ждёт оператора (``awaiting_operator``), система предлагает одному оператору — готовому к
работе (services/readiness.py), с подтверждённой квалификацией на тип БПЛА заказа (services/licenses.py)
и с наименьшей нагрузкой (предложенные ему заказы плюс принятые в работу; при равенстве — случайному).
Оператор принимает предложение или отклоняет его; отклонённый заказ сразу предлагается другому
подходящему оператору, отказавшемуся он больше не предлагается.

* Оператор отметил «Не готов к работе» — предложенные ему, но не принятые заказы сразу переходят к
  другим подходящим операторам; принятое задание остаётся за ним.
* Подходящего оператора нет — заказ ни у кого не показывается и ждёт: первый подходящий оператор,
  который появится (станет «Готов к работе», получит квалификацию, будет допущен), получит его.
* «Готов к работе» — фоновая задача (и сама отметка) выравнивает нагрузку: пока разница между самым
  загруженным и самым свободным подходящим оператором — 2 и больше, ему переходит последний
  поступивший из ещё не принятых заказов.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Assignment, Drone, Order
from . import audit, events, notifications, readiness

ACTIVE = ("accepted", "awaiting_readiness", "ready", "in_progress")
MAX_MOVES_PER_RUN = 20

_rng = random.SystemRandom()


def ready_operators(db: Session) -> List[uuid.UUID]:
    """Допущенные операторы, готовые к работе."""
    return readiness.only_ready(db, "operator", notifications.approved_operators(db))


def loads(db: Session, operators: Iterable[uuid.UUID]) -> Dict[uuid.UUID, int]:
    ops = list(operators)
    offered = Counter(db.execute(select(Order.offered_operator_id).where(
        Order.status == "awaiting_operator", Order.offered_operator_id.in_(ops))).scalars())
    active = Counter(db.execute(select(Order.operator_id).where(
        Order.status.in_(ACTIVE), Order.operator_id.in_(ops))).scalars())
    return {u: offered.get(u, 0) + active.get(u, 0) for u in ops}


def _declined(db: Session, order_id: uuid.UUID) -> Set[uuid.UUID]:
    return set(db.execute(select(Assignment.user_id).where(
        Assignment.order_id == order_id, Assignment.role == "operator", Assignment.status == "declined")).scalars())


class _Qualified:
    """Кэш квалификаций операторов на время одного распределения."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._models: Dict[uuid.UUID, Set[uuid.UUID]] = {}

    def ok(self, user_id: uuid.UUID, drone: Optional[Drone]) -> bool:
        if drone is None or drone.drone_model_id is None:
            return True                                    # дрон без типа из справочника — допуск не нужен
        if user_id not in self._models:
            from . import licenses
            self._models[user_id] = set(licenses.approved_models(self.db, user_id))
        return drone.drone_model_id in self._models[user_id]


def candidates(db: Session, order: Order, ops: Iterable[uuid.UUID], q: Optional[_Qualified] = None) -> List[uuid.UUID]:
    q = q or _Qualified(db)
    drone = db.get(Drone, order.drone_id)
    declined = _declined(db, order.id)
    return [u for u in ops if u not in declined and q.ok(u, drone)]


def _pick(load: Dict[uuid.UUID, int], allowed: Iterable[uuid.UUID]) -> Optional[uuid.UUID]:
    pool = {u: load[u] for u in allowed if u in load}
    if not pool:
        return None
    least = min(pool.values())
    return _rng.choice(sorted(u for u, n in pool.items() if n == least))


def _set(db: Session, order: Order, operator_id: Optional[uuid.UUID], reason: str) -> None:
    previous = order.offered_operator_id
    if previous == operator_id:
        return
    order.offered_operator_id = operator_id
    order.offered_at = clock.now() if operator_id else None
    order.version += 1
    audit.record(db, audit.Actor.system("operator_dispatch"), "order.operator_offered", entity_type="order",
                 entity_id=order.id, details={"before": previous, "after": operator_id, "reason": reason})
    events.publish(db, "order.operator_offered", {"order_id": str(order.id)},
                   users=[operator_id, previous, order.coordinator_id], roles=["admin"])
    if operator_id:
        notifications.create(db, [operator_id], "order.offered",
                             f"Вам предложен заказ №{order.number} — примите или отклоните его", reason,
                             order_id=order.id)
    if previous:
        notifications.create(db, [previous], "order.offered",
                             f"Заказ №{order.number} передан другому оператору", reason, order_id=order.id)


def offer(db: Session, order: Order, reason: str = "Автоматическое назначение: наименьшая нагрузка") -> Optional[uuid.UUID]:
    """Предложить заказ подходящему оператору с наименьшей нагрузкой; None — подходящих нет (заказ ждёт)."""
    if order.status != "awaiting_operator":
        if order.offered_operator_id is not None:
            order.offered_operator_id, order.offered_at = None, None
        return None
    ops = ready_operators(db)
    chosen = _pick(loads(db, ops), candidates(db, order, ops))
    _set(db, order, chosen, reason if chosen else "Нет готового оператора с нужной квалификацией — заказ ждёт")
    return chosen


def release_operator(db: Session, user_id: uuid.UUID) -> int:
    """Оператор ушёл со смены: его непринятые предложения — другим подходящим операторам."""
    moved = 0
    for o in db.execute(select(Order).where(Order.status == "awaiting_operator", Order.offered_operator_id == user_id)
                        .order_by(Order.submitted_at).with_for_update(of=Order)).unique().scalars():
        offer(db, o, "Оператор не готов к работе")
        moved += 1
    return moved


def sweep(db: Session) -> Dict[str, int]:
    """Назначение ждущих заказов, снятие с неподходящих операторов, выравнивание нагрузки."""
    stats = {"offered": 0, "moved": 0}
    ops = ready_operators(db)
    q = _Qualified(db)
    waiting = list(db.execute(select(Order).where(Order.status == "awaiting_operator")
                              .order_by(Order.submitted_at).with_for_update(of=Order, skip_locked=True)).unique().scalars())
    load = loads(db, ops)
    allowed = {o.id: candidates(db, o, ops, q) for o in waiting}
    # 1) без предложения или предложен тому, кто больше не подходит
    for o in waiting:
        if o.offered_operator_id is not None and o.offered_operator_id in allowed[o.id]:
            continue
        chosen = _pick(load, allowed[o.id])
        if chosen is None:
            if o.offered_operator_id is not None:
                _set(db, o, None, "Нет готового оператора с нужной квалификацией — заказ ждёт")
            continue
        _set(db, o, chosen, "Автоматическое назначение: наименьшая нагрузка" if o.offered_operator_id is None
             else "Прежний оператор недоступен")
        load[chosen] += 1
        stats["offered"] += 1
    # 2) выравнивание: последние поступившие непринятые заказы — к менее загруженным подходящим
    for _ in range(MAX_MOVES_PER_RUN):
        moved = False
        for busiest in sorted(load, key=lambda u: -load[u]):
            mine = sorted((o for o in waiting if o.offered_operator_id == busiest),
                          key=lambda o: -(o.submitted_at or o.created_at).timestamp())
            for o in mine:
                pool = [u for u in allowed[o.id] if u != busiest and load[u] <= load[busiest] - 2]
                target = _pick(load, pool)
                if target is None:
                    continue
                _set(db, o, target, "Выравнивание нагрузки операторов")
                load[busiest] -= 1
                load[target] += 1
                stats["moved"] += 1
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return stats
