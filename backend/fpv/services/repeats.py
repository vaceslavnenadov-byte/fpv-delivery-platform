"""Повторный вылет.

Заказчик просит повторить выполненный или выполняемый заказ (комментарий — по желанию); запрос
получает координатор заказа (если его нет — координаторы и администраторы). После согласования
создаётся новый заказ — копия маршрута (база, Б, промежуточные точки, В), груза, получателя и
выбранной карты — сразу в очереди операторов, с пометками «Приоритетный» и «Повторный»; в списках
операторов, техника парка и координатора он идёт первым. Координатор исходного заказа сразу
назначается и новому. Число запросов не ограничено.

Повторный вылет — новый заказ на тех же условиях: проверки (дрон, зона, ограничения полётов)
выполняются заново; занятость дрона исходным заказом не мешает — к старту оператор всё равно
проходит проверку готовности.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import Assignment, Order, OrderStatusHistory, RepeatRequest, Route
from . import audit, drones, events, notifications, orders

REPEATABLE = ("in_progress", "awaiting_delivery_confirmation", "admin_review", "completed", "disputed")
ACTIVE_FOR_PRIORITY = ("awaiting_operator", "accepted", "awaiting_readiness", "ready", "in_progress")


def _brief(db: Session, uid: Optional[uuid.UUID]) -> Optional[Dict[str, Any]]:
    from ..api import serialize
    return serialize.user_brief(db, uid)


def serialize(db: Session, r: RepeatRequest) -> Dict[str, Any]:
    new_number = None
    if r.new_order_id:
        n = db.get(Order, r.new_order_id)
        new_number = n.number if n else None
    return {"id": r.id, "order_id": str(r.order_id), "status": r.status, "comment": r.comment,
            "requested_by": _brief(db, r.requested_by), "created_at": r.created_at.isoformat() if r.created_at else None,
            "decided_by": _brief(db, r.decided_by), "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_comment": r.decision_comment,
            "new_order": {"id": str(r.new_order_id), "number": new_number} if r.new_order_id else None}


def for_order(db: Session, order_id: uuid.UUID) -> List[RepeatRequest]:
    return list(db.execute(select(RepeatRequest).where(RepeatRequest.order_id == order_id)
                           .order_by(RepeatRequest.id.desc())).scalars())


def _deciders(db: Session, order: Order) -> List[uuid.UUID]:
    if order.coordinator_id:
        return [order.coordinator_id]
    return notifications.users_with_role(db, "coordinator") + notifications.users_with_role(db, "admin")


def request(db: Session, p: orders.Principal, order_id: uuid.UUID, comment: str) -> RepeatRequest:
    p.require("order.repeat.request")
    order = orders.visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Повторный вылет запрашивает заказчик")
    if order.status not in REPEATABLE:
        raise Conflict("Повторный вылет можно запросить для выполняемого или выполненного заказа",
                       code="invalid_transition")
    r = RepeatRequest(order_id=order.id, requested_by=p.user_id, comment=(comment or "").strip()[:2000],
                      status="pending", created_at=clock.now())
    db.add(r)
    db.flush()
    audit.record(db, p.actor, "order.repeat_requested", entity_type="order", entity_id=order.id,
                 details={"request_id": r.id, "comment": r.comment})
    notifications.create(db, _deciders(db, order), "order.repeat",
                         f"Запрос повторного вылета по заказу №{order.number}", r.comment or "Без комментария",
                         order_id=order.id, severity="warning")
    events.publish(db, "order.repeat_requested", {"order_id": str(order.id)},
                   users=[order.customer_id, order.coordinator_id], roles=["admin"])
    return r


def _can_decide(p: orders.Principal, order: Order) -> None:
    p.require("order.repeat.decide")
    if order.coordinator_id and order.coordinator_id != p.user_id and not p.has("order.read_all"):
        raise Forbidden("Повторный вылет согласует координатор этого заказа")


def _clone(db: Session, settings: Settings, p: orders.Principal, src: Order) -> Order:
    now = clock.now()
    duration = max(src.window_end - src.window_start, dt.timedelta(minutes=settings.min_window_minutes))
    start = now + dt.timedelta(minutes=15)
    new = Order(customer_id=src.customer_id, drone_id=src.drone_id, status="draft",
                cargo_type=src.cargo_type, cargo_description=src.cargo_description, cargo_weight_g=src.cargo_weight_g,
                cargo_length_mm=src.cargo_length_mm, cargo_width_mm=src.cargo_width_mm,
                cargo_height_mm=src.cargo_height_mm, recipient_name=src.recipient_name,
                recipient_phone=src.recipient_phone, window_start=start, window_end=start + duration,
                map_provider_id=src.map_provider_id, repeat_of=src.id, priority=True,
                created_at=now, updated_at=now)
    db.add(new)
    db.flush()
    orders.save_cargo_items(db, new, [{f: (i.description if f == "description" else getattr(i, f))
                                       for f in orders.CARGO_FIELDS} for i in orders.cargo_items(db, src)])
    r = src.route
    db.add(Route(order_id=new.id, origin_lat=r.origin_lat, origin_lon=r.origin_lon, origin_address=r.origin_address,
                 dest_lat=r.dest_lat, dest_lon=r.dest_lon, dest_address=r.dest_address, distance_m=r.distance_m,
                 max_altitude_m=r.max_altitude_m, checkpoints=r.checkpoints, stops=r.stops or [],
                 base_lat=r.base_lat, base_lon=r.base_lon, base_address=r.base_address, return_m=r.return_m))
    db.add(OrderStatusHistory(order_id=new.id, from_status=None, to_status="draft", actor_id=p.user_id,
                              actor_kind="user", reason=f"Повторный вылет заказа №{src.number}", created_at=now))
    db.flush()
    db.refresh(new)
    return new


def decide(db: Session, settings: Settings, p: orders.Principal, request_id: int, approve: bool,
           comment: str) -> RepeatRequest:
    r = db.execute(select(RepeatRequest).where(RepeatRequest.id == request_id).with_for_update()).scalar_one_or_none()
    if r is None:
        raise NotFound("Запрос не найден")
    src = orders.get_order(db, r.order_id, for_update=True)
    _can_decide(p, src)
    if r.status != "pending":
        raise Conflict("Запрос уже рассмотрен", code="already_decided")
    comment = (comment or "").strip()
    if not approve and len(comment) < 3:
        raise ValidationFailed("Укажите причину отказа (не менее 3 символов)")
    if approve:
        new = _clone(db, settings, p, src)
        drone = drones.get_drone(db, new.drone_id)
        result = orders.validate_order(db, settings, new, drone)
        failed = [c for c in result["checks"] if not c["ok"] and c["code"] not in ("drone_available",)]
        if failed:
            raise Conflict("Повторный вылет невозможен: " + "; ".join(c["message"] for c in failed),
                           code="repeat_invalid", details={"checks": failed})
        new.validation_result = result
        new.route.approval_status, new.route.approval_notes, new.route.approved_at = "approved", [], clock.now()
        new.quote_amount = orders.quote(settings, new.route.distance_m, new.cargo_weight_g)
        new.submitted_at = clock.now()
        coordinator = src.coordinator_id or (p.user_id if "coordinator" in p.roles else None)
        if coordinator:
            new.coordinator_id = coordinator
            db.add(Assignment(order_id=new.id, user_id=coordinator, role="coordinator", created_by=p.user_id,
                              created_at=clock.now()))
        orders.transition(db, new, "pending_confirmation", p.actor, reason="Повторный вылет: проверки пройдены")
        orders.transition(db, new, "awaiting_operator", p.actor,
                          reason=f"Повторный вылет заказа №{src.number} согласован — приоритетный")
        r.new_order_id = new.id
    r.status = "approved" if approve else "rejected"
    r.decided_by, r.decided_at, r.decision_comment = p.user_id, clock.now(), comment or None
    db.flush()
    audit.record(db, p.actor, "order.repeat_approved" if approve else "order.repeat_rejected", entity_type="order",
                 entity_id=src.id, details={"request_id": r.id, "comment": comment,
                                            "new_order_id": str(r.new_order_id) if r.new_order_id else None})
    if approve:
        new_number = db.get(Order, r.new_order_id).number
        notifications.create(db, [src.customer_id], "order.repeat",
                             f"Повторный вылет согласован: заказ №{new_number}", comment, order_id=r.new_order_id)
        from . import fleets
        tech = fleets.fleet_technician(db, drones.get_drone(db, src.drone_id))
        notifications.create(db, [t for t in [tech, src.operator_id] if t], "order.repeat",
                             f"Приоритетный повторный заказ №{new_number}",
                             f"Повтор заказа №{src.number}", order_id=r.new_order_id, severity="warning")
    else:
        notifications.create(db, [src.customer_id], "order.repeat",
                             f"Повторный вылет по заказу №{src.number} не согласован", comment, order_id=src.id,
                             severity="warning")
    events.publish(db, "order.repeat_decided", {"order_id": str(src.id)},
                   users=[src.customer_id, src.coordinator_id], roles=["admin"])
    return r


def pending_for(db: Session, p: orders.Principal) -> List[RepeatRequest]:
    """Запросы, ждущие решения этого координатора (администратору — все)."""
    p.require("order.repeat.decide")
    stmt = select(RepeatRequest).where(RepeatRequest.status == "pending")
    if not p.has("order.read_all"):
        stmt = stmt.join(Order, Order.id == RepeatRequest.order_id).where(Order.coordinator_id == p.user_id)
    return list(db.execute(stmt.order_by(RepeatRequest.id)).scalars())
