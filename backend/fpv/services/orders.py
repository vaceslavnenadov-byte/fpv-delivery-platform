"""Order Management Service: жизненный цикл заказа и конечный автомат статусов.

Все переходы выполняются только через ``transition`` под блокировкой строки
заказа (SELECT ... FOR UPDATE). Недопустимый переход отклоняется (409),
история изменений неизменяема (триггер БД), каждое изменение публикуется
в outbox и в канал реального времени.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import (Assignment, Complaint, DeliveryConfirmation, Drone, Order, OrderNote,
                      OrderStatusHistory, Role, Route, User, UserRole)
from . import audit, drones, events, geo
from .identity import Principal

ACTIVE_EXECUTION = ("accepted", "awaiting_readiness", "ready", "in_progress")
FINAL = ("completed", "rejected", "cancelled", "aborted_technical")

# Допустимые переходы: (из, в).
TRANSITIONS = {
    ("draft", "pending_confirmation"),            # заказчик отправил, проверки пройдены
    ("draft", "cancelled"),
    ("pending_confirmation", "awaiting_operator"),  # заказчик подтвердил условия
    ("pending_confirmation", "cancelled"),
    ("awaiting_operator", "accepted"),            # оператор принял
    ("awaiting_operator", "cancelled"),
    ("awaiting_operator", "rejected"),            # администратор отклонил заказ
    ("accepted", "awaiting_readiness"),
    ("accepted", "awaiting_operator"),            # оператор отказался от задания
    ("accepted", "cancelled"),
    ("awaiting_readiness", "ready"),
    ("awaiting_readiness", "awaiting_operator"),
    ("awaiting_readiness", "cancelled"),
    ("awaiting_readiness", "aborted_technical"),
    ("ready", "in_progress"),
    ("ready", "awaiting_readiness"),              # повторная проверка не пройдена
    ("ready", "awaiting_operator"),
    ("ready", "cancelled"),
    ("ready", "aborted_technical"),
    ("in_progress", "awaiting_delivery_confirmation"),  # событие завершения доставки
    ("in_progress", "aborted_technical"),
    ("awaiting_delivery_confirmation", "admin_review"),
    ("awaiting_delivery_confirmation", "disputed"),
    ("admin_review", "completed"),
    ("admin_review", "rejected"),
    ("admin_review", "disputed"),
    ("completed", "disputed"),
    ("disputed", "admin_review"),
    ("disputed", "completed"),
    ("disputed", "rejected"),
}

CUSTOMER_CANCELLABLE = ("draft", "pending_confirmation", "awaiting_operator", "accepted",
                        "awaiting_readiness", "ready")


# ---------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------

def get_order(db: Session, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if for_update:
        stmt = stmt.with_for_update(of=Order)
    order = db.execute(stmt).unique().scalar_one_or_none()
    if order is None:
        raise NotFound("Заказ не найден")
    return order


def can_view(db: Session, p: Principal, order: Order) -> bool:
    if p.has("order.read_all"):
        return True
    if p.has("order.read_own") and order.customer_id == p.user_id:
        return True
    if p.has("order.read_assigned") and p.user_id in (order.operator_id, order.coordinator_id):
        return True
    if p.has("order.queue.read") and order.status == "awaiting_operator":
        return True
    return False


def visible_order(db: Session, p: Principal, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    order = get_order(db, order_id, for_update=for_update)
    if not can_view(db, p, order):
        # 404 вместо 403: не раскрываем существование чужих заказов.
        raise NotFound("Заказ не найден")
    return order


def _check_version(order: Order, expected_version: Optional[int]) -> None:
    if expected_version is not None and expected_version != order.version:
        raise Conflict("Заказ был изменён другим пользователем. Обновите данные.", code="version_conflict",
                       details={"current_version": order.version})


# ---------------------------------------------------------------------------
# Переходы
# ---------------------------------------------------------------------------

def transition(db: Session, order: Order, to_status: str, actor: audit.Actor, *, actor_kind: str = "user",
               reason: str = "") -> None:
    from_status = order.status
    if (from_status, to_status) not in TRANSITIONS:
        raise Conflict(f"Недопустимый переход статуса: {from_status} → {to_status}", code="invalid_transition",
                       details={"from": from_status, "to": to_status})
    now = clock.now()
    order.status = to_status
    order.version += 1
    order.updated_at = now
    db.add(OrderStatusHistory(order_id=order.id, from_status=from_status, to_status=to_status,
                              actor_id=actor.user_id, actor_kind=actor_kind, reason=reason[:1000],
                              created_at=now))
    db.flush()
    audit.record(db, actor, "order.status_changed", entity_type="order", entity_id=order.id,
                 details={"from": from_status, "to": to_status, "reason": reason})
    events.publish(db, "order.status_changed",
                   {"order_id": str(order.id), "from_status": from_status, "to_status": to_status,
                    "reason": reason, "version": order.version},
                   users=[order.customer_id, order.operator_id, order.coordinator_id],
                   roles=["admin"] + (["operator"] if "awaiting_operator" in (from_status, to_status) else []))


# ---------------------------------------------------------------------------
# Проверка заказа
# ---------------------------------------------------------------------------

def quote(settings: Settings, distance_m: int, weight_g: int) -> Decimal:
    amount = Decimal(str(settings.price_base)) \
        + Decimal(str(settings.price_per_km)) * Decimal(distance_m) / Decimal(1000) \
        + Decimal(str(settings.price_per_kg)) * Decimal(weight_g) / Decimal(1000)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_order(db: Session, settings: Settings, order: Order, drone: Drone) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def check(code: str, ok: bool, message: str) -> None:
        checks.append({"code": code, "ok": bool(ok), "message": message})

    r = order.route
    text_blob = f"{order.cargo_type} {order.cargo_description}".lower()
    check("cargo_type", order.cargo_type in settings.allowed_cargo_types,
          "Тип груза разрешён" if order.cargo_type in settings.allowed_cargo_types else "Тип груза не разрешён")
    bad = [k for k in settings.prohibited_keywords if k.lower() in text_blob]
    check("cargo_prohibited", not bad, "Запрещённые категории не обнаружены" if not bad
          else "Описание груза содержит признаки запрещённой категории")
    check("cargo_weight", order.cargo_weight_g <= settings.max_cargo_weight_g,
          f"Масса груза не более {settings.max_cargo_weight_g} г")
    max_dim = max(order.cargo_length_mm, order.cargo_width_mm, order.cargo_height_mm)
    check("cargo_dimensions", max_dim <= settings.max_cargo_dim_mm,
          f"Габариты не более {settings.max_cargo_dim_mm} мм по любой стороне")
    check("service_area", geo.in_bbox(r.origin_lat, r.origin_lon, settings.service_area)
          and geo.in_bbox(r.dest_lat, r.dest_lon, settings.service_area),
          "Точки А и Б в зоне обслуживания")
    check("distance", 0 < r.distance_m <= settings.max_distance_m,
          f"Расстояние маршрута {r.distance_m} м (допустимо до {settings.max_distance_m} м, точки не совпадают)")
    violated = []
    for z in settings.no_fly_zones:
        d = geo.segment_point_distance_m((r.origin_lat, r.origin_lon), (r.dest_lat, r.dest_lon), (z.lat, z.lon))
        if d < z.radius_m:
            violated.append(z.name)
    check("airspace", not violated, "Маршрут не пересекает зоны ограничения полётов" if not violated
          else "Маршрут пересекает зоны ограничения: " + ", ".join(violated))
    now = clock.now()
    window_ok = order.window_start > now and \
        (order.window_end - order.window_start) >= dt.timedelta(minutes=settings.min_window_minutes)
    check("time_window", window_ok,
          f"Временное окно в будущем и не короче {settings.min_window_minutes} мин")
    ok_drone, reasons = drones.readiness(db, settings, drone, cargo_weight_g=order.cargo_weight_g,
                                         distance_m=r.distance_m * 2, require_online=False)
    check("drone", ok_drone, "Дрон допущен к эксплуатации" if ok_drone else "; ".join(reasons))
    busy = db.execute(select(Order.id).where(Order.drone_id == drone.id, Order.id != order.id,
                                             Order.status.in_(ACTIVE_EXECUTION))).first()
    check("drone_available", busy is None, "Дрон свободен" if busy is None else "Дрон занят другим заказом")
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "checked_at": now.isoformat()}


# ---------------------------------------------------------------------------
# Действия заказчика
# ---------------------------------------------------------------------------

def create_draft(db: Session, settings: Settings, p: Principal, data: Dict[str, Any]) -> Order:
    p.require("order.create")
    drone = db.get(Drone, data["drone_id"])
    if drone is None or drone.owner_id != p.user_id:
        raise ValidationFailed("Выберите собственный зарегистрированный дрон", code="drone_invalid")
    o, d = data["origin"], data["destination"]
    distance = int(round(geo.haversine_m(o["lat"], o["lon"], d["lat"], d["lon"])))
    order = Order(customer_id=p.user_id, drone_id=drone.id, status="draft",
                  cargo_type=data["cargo_type"], cargo_description=data["cargo_description"],
                  cargo_weight_g=data["cargo_weight_g"], cargo_length_mm=data["cargo_length_mm"],
                  cargo_width_mm=data["cargo_width_mm"], cargo_height_mm=data["cargo_height_mm"],
                  recipient_name=data["recipient_name"], recipient_phone=data["recipient_phone"],
                  window_start=data["window_start"], window_end=data["window_end"],
                  created_at=clock.now(), updated_at=clock.now())
    db.add(order)
    db.flush()
    checkpoints = [{"name": f"КТ-{i}", "lat": round(lat, 6), "lon": round(lon, 6)}
                   for i, (lat, lon) in enumerate(
                       (geo.interpolate((o["lat"], o["lon"]), (d["lat"], d["lon"]), t) for t in (0.25, 0.5, 0.75)),
                       start=1)]
    db.add(Route(order_id=order.id, origin_lat=o["lat"], origin_lon=o["lon"], origin_address=o["address"],
                 dest_lat=d["lat"], dest_lon=d["lon"], dest_address=d["address"], distance_m=distance,
                 checkpoints=checkpoints))
    db.add(OrderStatusHistory(order_id=order.id, from_status=None, to_status="draft", actor_id=p.user_id,
                              actor_kind="user", reason="Создан черновик", created_at=clock.now()))
    db.flush()
    db.refresh(order)
    audit.record(db, p.actor, "order.created", entity_type="order", entity_id=order.id,
                 details={"drone_id": drone.id, "distance_m": distance})
    return order


def submit(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID,
           expected_version: Optional[int] = None) -> Order:
    p.require("order.create")
    order = visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Отправить заказ может только заказчик")
    _check_version(order, expected_version)
    if order.status != "draft":
        raise Conflict("Отправить можно только черновик", code="invalid_transition")
    drone = drones.get_drone(db, order.drone_id)
    result = validate_order(db, settings, order, drone)
    order.validation_result = result
    route = order.route
    if not result["ok"]:
        route.approval_status = "rejected"
        route.approval_notes = [c for c in result["checks"] if not c["ok"]]
        order.version += 1
        audit.record(db, p.actor, "order.validation_failed", entity_type="order", entity_id=order.id,
                     outcome="denied", details={"failed": [c["code"] for c in result["checks"] if not c["ok"]]})
        db.flush()
        return order
    route.approval_status = "approved"
    route.approval_notes = []
    route.approved_at = clock.now()
    order.quote_amount = quote(settings, route.distance_m, order.cargo_weight_g)
    order.submitted_at = clock.now()
    transition(db, order, "pending_confirmation", p.actor, reason="Проверки пройдены, рассчитана стоимость")
    return order


def confirm(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID,
            expected_version: Optional[int] = None) -> Order:
    p.require("order.create")
    order = visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Подтвердить заказ может только заказчик")
    _check_version(order, expected_version)
    if order.status != "pending_confirmation":
        raise Conflict("Заказ не ожидает подтверждения", code="invalid_transition")
    drone = drones.get_drone(db, order.drone_id)
    ok, reasons = drones.readiness(db, settings, drone, cargo_weight_g=order.cargo_weight_g, require_online=False)
    if not ok:
        raise ValidationFailed("Дрон больше не допущен к выполнению: " + "; ".join(reasons), code="drone_not_ready")
    transition(db, order, "awaiting_operator", p.actor, reason="Заказчик подтвердил условия")
    return order


def cancel(db: Session, p: Principal, order_id: uuid.UUID, reason: str,
           expected_version: Optional[int] = None) -> Order:
    order = visible_order(db, p, order_id, for_update=True)
    _check_version(order, expected_version)
    is_owner = order.customer_id == p.user_id and p.has("order.cancel_own")
    is_admin = p.has("order.review")
    if not (is_owner or is_admin):
        raise Forbidden("Недостаточно прав для отмены заказа")
    if order.status not in CUSTOMER_CANCELLABLE:
        raise Conflict("Заказ нельзя отменить в текущем статусе", code="invalid_transition")
    _end_assignments(db, order, "released", reason or "Заказ отменён")
    order.closed_reason = reason or "Отменён"
    transition(db, order, "cancelled", p.actor, reason=reason or "Отменён заказчиком")
    return order


def confirm_receipt(db: Session, p: Principal, order_id: uuid.UUID, received: bool, comment: str) -> Order:
    p.require("order.confirm_receipt")
    order = visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Подтвердить получение может только заказчик")
    if order.status != "awaiting_delivery_confirmation":
        raise Conflict("Заказ не ожидает подтверждения получения", code="invalid_transition")
    db.add(DeliveryConfirmation(order_id=order.id, kind="recipient_receipt", actor_id=p.user_id,
                                decision="confirmed" if received else "rejected", comment=comment,
                                created_at=clock.now()))
    db.flush()
    if received:
        transition(db, order, "admin_review", p.actor, reason="Получатель подтвердил получение")
    else:
        _open_dispute(db, p, order, comment or "Груз не получен")
    return order


def _open_dispute(db: Session, p: Principal, order: Order, description: str) -> Complaint:
    if db.execute(select(Complaint.id).where(Complaint.order_id == order.id, Complaint.kind == "dispute",
                                             Complaint.status == "open")).first():
        raise Conflict("По заказу уже открыт спор", code="dispute_exists")
    complaint = Complaint(order_id=order.id, author_id=p.user_id, kind="dispute", description=description,
                          created_at=clock.now())
    db.add(complaint)
    db.flush()
    order.status_before_dispute = order.status
    transition(db, order, "disputed", p.actor, reason=description[:300])
    audit.record(db, p.actor, "complaint.created", entity_type="complaint", entity_id=complaint.id,
                 details={"order_id": order.id, "kind": "dispute"})
    return complaint


def open_dispute(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID, description: str) -> Complaint:
    p.require("complaint.create")
    order = visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Спор может открыть только заказчик")
    if order.status not in ("awaiting_delivery_confirmation", "admin_review", "completed"):
        raise Conflict("Спор по заказу в текущем статусе невозможен", code="invalid_transition")
    if order.status == "completed" and order.completed_at and \
            clock.now() - order.completed_at > dt.timedelta(days=settings.dispute_window_days):
        raise Conflict(f"Срок подачи спора ({settings.dispute_window_days} дн.) истёк", code="dispute_window_closed")
    return _open_dispute(db, p, order, description)


# ---------------------------------------------------------------------------
# Действия оператора
# ---------------------------------------------------------------------------

def queue(db: Session, p: Principal, *, max_distance_m: Optional[int] = None,
          cargo_type: Optional[str] = None) -> List[Order]:
    p.require("order.queue.read")
    declined = select(Assignment.order_id).where(Assignment.user_id == p.user_id, Assignment.role == "operator",
                                                 Assignment.status == "declined")
    stmt = select(Order).join(Route, Route.order_id == Order.id).where(
        Order.status == "awaiting_operator", Order.id.notin_(declined))
    if max_distance_m:
        stmt = stmt.where(Route.distance_m <= max_distance_m)
    if cargo_type:
        stmt = stmt.where(Order.cargo_type == cargo_type)
    return list(db.execute(stmt.order_by(Order.window_start)).unique().scalars())


def my_orders(db: Session, p: Principal, status: Optional[str] = None, as_role: Optional[str] = None) -> List[Order]:
    """Заказы пользователя. ``as_role`` — только в одной роли (клиент роли не смешивает
    собственные заказы пользователя с назначенными ему, если ролей несколько)."""
    conds = []
    if p.has("order.read_own") and as_role in (None, "customer"):
        conds.append(Order.customer_id == p.user_id)
    if p.has("order.read_assigned"):
        if as_role in (None, "operator"):
            conds.append(Order.operator_id == p.user_id)
        if as_role in (None, "coordinator"):
            conds.append(Order.coordinator_id == p.user_id)
    if not conds:
        raise Forbidden("Недостаточно прав")
    stmt = select(Order).where(or_(*conds))
    if status:
        stmt = stmt.where(Order.status == status)
    return list(db.execute(stmt.order_by(Order.created_at.desc()).limit(500)).unique().scalars())


def _active_assignment(db: Session, order: Order, role: str) -> Optional[Assignment]:
    return db.execute(select(Assignment).where(Assignment.order_id == order.id, Assignment.role == role,
                                               Assignment.status == "active")).scalar_one_or_none()


def _end_assignments(db: Session, order: Order, status: str, reason: str, role: Optional[str] = None) -> None:
    stmt = select(Assignment).where(Assignment.order_id == order.id, Assignment.status == "active")
    if role:
        stmt = stmt.where(Assignment.role == role)
    for a in db.execute(stmt).scalars():
        a.status = status
        a.reason = reason
        a.ended_at = clock.now()


def _require_own_operator(p: Principal, order: Order) -> None:
    if order.operator_id != p.user_id:
        raise Forbidden("Заказ назначен другому оператору")


def accept(db: Session, p: Principal, order_id: uuid.UUID, expected_version: Optional[int] = None) -> Order:
    p.require("order.accept")
    order = visible_order(db, p, order_id, for_update=True)
    _check_version(order, expected_version)
    if order.status != "awaiting_operator":
        raise Conflict("Заказ уже принят или недоступен", code="invalid_transition")
    busy = db.execute(select(Order.id).where(Order.operator_id == p.user_id,
                                             Order.status.in_(ACTIVE_EXECUTION))).first()
    if busy:
        raise Conflict("У оператора уже есть активное задание", code="operator_busy")
    order.operator_id = p.user_id
    order.accepted_at = clock.now()
    db.add(Assignment(order_id=order.id, user_id=p.user_id, role="operator", created_by=p.user_id,
                      created_at=clock.now()))
    db.flush()
    transition(db, order, "accepted", p.actor, reason="Заказ принят оператором")
    return order


def decline(db: Session, p: Principal, order_id: uuid.UUID, reason: str,
            expected_version: Optional[int] = None) -> Order:
    p.require("order.accept")
    order = visible_order(db, p, order_id, for_update=True)
    _check_version(order, expected_version)
    if order.status == "awaiting_operator":
        exists = db.execute(select(Assignment.id).where(
            Assignment.order_id == order.id, Assignment.user_id == p.user_id, Assignment.role == "operator",
            Assignment.status == "declined")).first()
        if not exists:
            db.add(Assignment(order_id=order.id, user_id=p.user_id, role="operator", status="declined",
                              reason=reason, created_by=p.user_id, created_at=clock.now(), ended_at=clock.now()))
        audit.record(db, p.actor, "order.declined", entity_type="order", entity_id=order.id,
                     details={"reason": reason})
        return order
    if order.status in ("accepted", "awaiting_readiness", "ready"):
        _require_own_operator(p, order)
        _end_assignments(db, order, "released", reason or "Оператор отказался от задания", role="operator")
        order.operator_id = None
        transition(db, order, "awaiting_operator", p.actor, reason=reason or "Оператор отказался от задания")
        return order
    raise Conflict("Отказ от заказа в текущем статусе невозможен", code="invalid_transition")


def check_readiness(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID) -> Tuple[Order, List[str]]:
    p.require("order.execute")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    if order.status not in ("accepted", "awaiting_readiness", "ready"):
        raise Conflict("Проверка готовности недоступна в текущем статусе", code="invalid_transition")
    drone = drones.get_drone(db, order.drone_id)
    ok, reasons = drones.readiness(db, settings, drone, cargo_weight_g=order.cargo_weight_g,
                                   distance_m=order.route.distance_m * 2)
    if order.status == "accepted":
        transition(db, order, "awaiting_readiness", p.actor, reason="Запрошена проверка готовности")
    if ok and order.status == "awaiting_readiness":
        transition(db, order, "ready", audit.Actor.system("readiness-check"), actor_kind="system",
                   reason="Проверка готовности пройдена")
    elif not ok and order.status == "ready":
        transition(db, order, "awaiting_readiness", audit.Actor.system("readiness-check"), actor_kind="system",
                   reason="; ".join(reasons))
    audit.record(db, p.actor, "order.readiness_checked", entity_type="order", entity_id=order.id,
                 details={"ok": ok, "reasons": reasons})
    return order, reasons


def start(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID,
          expected_version: Optional[int] = None) -> Order:
    from . import video
    p.require("order.execute")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    _check_version(order, expected_version)
    if order.status != "ready":
        raise Conflict("Начать можно только заказ в статусе «Готов к выполнению»", code="invalid_transition")
    drone = drones.get_drone(db, order.drone_id)
    ok, reasons = drones.readiness(db, settings, drone, cargo_weight_g=order.cargo_weight_g,
                                   distance_m=order.route.distance_m * 2)
    if not ok:
        transition(db, order, "awaiting_readiness", audit.Actor.system("readiness-check"), actor_kind="system",
                   reason="; ".join(reasons))
        return order
    order.started_at = clock.now()
    transition(db, order, "in_progress", p.actor, reason="Задание начато оператором")
    video.open_buffer(settings, order.id)
    return order


def complete(db: Session, settings: Settings, actor: audit.Actor, order: Order, *, actor_kind: str = "user",
             event_at: Optional[dt.datetime] = None) -> Order:
    """Событие завершения доставки (оператор или сервисный модуль дрона). Идемпотентно."""
    from . import video
    if order.status != "in_progress":
        if order.delivered_at is not None:
            return order          # повторное событие завершения — без изменений
        raise Conflict("Заказ не выполняется", code="invalid_transition")
    event_at = event_at or clock.now()
    order.delivered_at = event_at
    video.capture_clip(db, settings, order, event_at, actor, trigger="delivery_completed")
    transition(db, order, "awaiting_delivery_confirmation", actor, actor_kind=actor_kind,
               reason="Доставка завершена, ожидается подтверждение получения")
    _end_assignments(db, order, "finished", "Задание выполнено", role="operator")
    return order


def operator_complete(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID) -> Order:
    p.require("order.execute")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    return complete(db, settings, p.actor, order)


def abort(db: Session, settings: Settings, actor: audit.Actor, order: Order, reason: str, *,
          actor_kind: str = "user", mark_drone: bool = True) -> Order:
    from . import video
    if order.status not in ("awaiting_readiness", "ready", "in_progress"):
        raise Conflict("Прерывание недоступно в текущем статусе", code="invalid_transition")
    if not reason:
        raise ValidationFailed("Укажите причину прерывания")
    was_in_progress = order.status == "in_progress"
    order.closed_reason = reason
    if was_in_progress:
        video.capture_clip(db, settings, order, clock.now(), actor, trigger="aborted")
    transition(db, order, "aborted_technical", actor, actor_kind=actor_kind, reason=reason)
    _end_assignments(db, order, "finished", reason, role="operator")
    if mark_drone:
        drone = drones.get_drone(db, order.drone_id, for_update=True)
        if drone.health_status == "serviceable":
            drone.health_status = "requires_check"
            audit.record(db, actor, "drone.health_status_changed", entity_type="drone", entity_id=drone.id,
                         details={"before": "serviceable", "after": "requires_check",
                                  "source": "order_aborted", "order_id": order.id})
    return order


def operator_abort(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID, reason: str) -> Order:
    p.require("order.execute")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    return abort(db, settings, p.actor, order, reason)


def submit_report(db: Session, p: Principal, order_id: uuid.UUID, data: Dict[str, Any]) -> DeliveryConfirmation:
    p.require("order.report")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    if order.status not in ("awaiting_delivery_confirmation", "admin_review", "aborted_technical", "disputed"):
        raise Conflict("Отчёт можно сформировать после завершения или прерывания задания", code="invalid_transition")
    if db.execute(select(DeliveryConfirmation.id).where(DeliveryConfirmation.order_id == order.id,
                                                        DeliveryConfirmation.kind == "operator_report")).first():
        raise Conflict("Отчёт уже сформирован", code="report_exists")
    rec = DeliveryConfirmation(order_id=order.id, kind="operator_report", actor_id=p.user_id, decision="submitted",
                               comment=data.get("summary", ""), report=data, created_at=clock.now())
    db.add(rec)
    db.flush()
    audit.record(db, p.actor, "order.report_submitted", entity_type="order", entity_id=order.id)
    events.notify(db, "order.updated", {"order_id": order.id}, users=[order.customer_id], roles=["admin"])
    return rec


# ---------------------------------------------------------------------------
# Координатор и администратор
# ---------------------------------------------------------------------------

def assign_coordinator(db: Session, p: Principal, order_id: uuid.UUID, coordinator_id: uuid.UUID) -> Order:
    p.require("order.assign")
    order = get_order(db, order_id, for_update=True)
    if order.status in FINAL:
        raise Conflict("Заказ завершён")
    has_role = db.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(
        UserRole.user_id == coordinator_id, Role.code == "coordinator")).first()
    user = db.get(User, coordinator_id)
    if not has_role or user is None or user.status != "active":
        raise ValidationFailed("Пользователь не является активным координатором")
    _end_assignments(db, order, "released", "Переназначение координатора", role="coordinator")
    db.flush()
    db.add(Assignment(order_id=order.id, user_id=coordinator_id, role="coordinator", created_by=p.user_id,
                      created_at=clock.now()))
    previous = order.coordinator_id
    order.coordinator_id = coordinator_id
    order.version += 1
    audit.record(db, p.actor, "order.coordinator_assigned", entity_type="order", entity_id=order.id,
                 details={"before": previous, "after": coordinator_id})
    events.publish(db, "order.coordinator_assigned", {"order_id": str(order.id)},
                   users=[coordinator_id, order.operator_id, order.customer_id], roles=["admin"])
    from . import notifications
    notifications.create(db, [coordinator_id], "order.assigned",
                         f"Вам назначен заказ №{order.number} для координации", order_id=order.id)
    return order


def add_note(db: Session, p: Principal, order_id: uuid.UUID, kind: str, body: str, severity: str) -> OrderNote:
    p.require("order.note.write")
    order = visible_order(db, p, order_id)
    if order.coordinator_id != p.user_id and not p.has("order.read_all"):
        raise Forbidden("Заказ не назначен вам")
    note = OrderNote(order_id=order.id, author_id=p.user_id, kind=kind, body=body, severity=severity,
                     created_at=clock.now())
    db.add(note)
    db.flush()
    audit.record(db, p.actor, f"order.note.{kind}", entity_type="order", entity_id=order.id,
                 details={"severity": severity})
    from . import notifications
    targets = [order.operator_id] if kind == "message_to_operator" else []
    if kind in ("incident", "deviation"):
        targets += notifications.users_with_role(db, "admin")
    if targets:
        notifications.create(db, targets, f"order.{kind}", f"Заказ №{order.number}: сообщение координатора",
                             body[:300], order_id=order.id, severity=severity)
    events.notify(db, "order.note_added", {"order_id": order.id},
                  users=[order.operator_id, order.coordinator_id], roles=["admin"])
    return note


def admin_review(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID, *, approve: bool,
                 comment: str, video_verified: Optional[bool], expected_version: Optional[int] = None) -> Order:
    """Проверка администратором: принятие/отклонение отчёта, проверка видео и окончательное начисление баллов."""
    from . import scoring, video
    p.require("order.review")
    order = get_order(db, order_id, for_update=True)
    _check_version(order, expected_version)
    if order.status != "admin_review":
        raise Conflict("Заказ не находится на проверке администратора", code="invalid_transition")
    if not approve and not comment:
        raise ValidationFailed("Укажите причину отклонения")
    if video_verified is not None:
        video.review(db, p, order.id, approved=video_verified, comment=comment)
    db.add(DeliveryConfirmation(order_id=order.id, kind="admin_review", actor_id=p.user_id,
                                decision="confirmed" if approve else "rejected", comment=comment,
                                created_at=clock.now()))
    db.flush()
    if approve:
        order.completed_at = clock.now()
        transition(db, order, "completed", p.actor, reason=comment or "Выполнение подтверждено администратором")
    else:
        order.closed_reason = comment
        transition(db, order, "rejected", p.actor, reason=comment)
    scoring.finalize(db, p, order)
    return order


def history(db: Session, order_id: uuid.UUID) -> List[OrderStatusHistory]:
    return list(db.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
                           .order_by(OrderStatusHistory.id)).scalars())
