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

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import (Assignment, Complaint, DeliveryConfirmation, Drone, Fleet, Order, OrderCargoItem, OrderNote,
                      OrderStatusHistory, Role, Route, User, UserRole)
from . import audit, drones, events, geo
from .identity import Principal

ACTIVE_EXECUTION = ("accepted", "awaiting_readiness", "ready", "in_progress")
FINAL = ("completed", "rejected", "cancelled", "aborted_technical")

# Допустимые переходы: (из, в).
TRANSITIONS = {
    ("draft", "pending_confirmation"),            # заказчик отправил, проверки пройдены
    ("draft", "cancelled"),
    ("pending_confirmation", "awaiting_coordinator"),  # заказчик разместил заказ — согласует координатор
    ("pending_confirmation", "awaiting_operator"),  # повторный вылет, уже согласованный координатором
    ("pending_confirmation", "cancelled"),
    ("awaiting_coordinator", "awaiting_operator"),  # координатор согласовал
    ("awaiting_coordinator", "rejected"),           # координатор отклонил
    ("awaiting_coordinator", "cancelled"),
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

ASAP_WINDOW_H = 24                 # заказ без времени доставки: окно «как можно скорее» на сутки
POINT_LETTERS = "БВГДЕЖЗИКЛМН"

CUSTOMER_CANCELLABLE = ("draft", "pending_confirmation", "awaiting_coordinator", "awaiting_operator", "accepted",
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


# Порядок во всех списках заказов (незавершённые заказы): наивысший приоритет грузополучателя,
# повторный вылет, высокий, обычный, низкий; завершённые — после.
PRIORITY_LEVELS = ("low", "normal", "high", "highest")
PRIORITY_FIRST = case(
    (Order.status.in_(FINAL), 9),
    (Order.priority_level == "highest", 0),
    (Order.repeat_of.is_not(None), 1),
    (Order.priority_level == "high", 2),
    (Order.priority_level == "normal", 3),
    else_=4)


def can_view(db: Session, p: Principal, order: Order) -> bool:
    if p.has("order.read_all"):
        return True
    if p.has("order.read_own") and order.customer_id == p.user_id:
        return True
    if p.has("order.read_assigned") and p.user_id in (order.operator_id, order.coordinator_id):
        return True
    if p.has("order.queue.read") and order.status == "awaiting_operator" and order.offered_operator_id == p.user_id:
        return True                                       # заказ предложен этому оператору
    if p.has("order.approve") and order.status == "awaiting_coordinator" and order.coordinator_id is None:
        return True           # ждёт согласования, свободных координаторов не было — может взять любой
    if p.has("order.read_fleet"):
        from . import fleets
        return fleets.is_fleet_technician_of(db, p, order)
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
    if to_status == "awaiting_operator" or from_status == "awaiting_operator":
        # Оператора назначает система: готовому, с квалификацией, с наименьшей нагрузкой.
        from . import operator_dispatch
        operator_dispatch.offer(db, order)
    if to_status == "awaiting_operator":
        from . import tech_cover
        tech_cover.cover(db, order)                       # техник парка не на смене — технику зоны


# ---------------------------------------------------------------------------
# Проверка заказа
# ---------------------------------------------------------------------------

def quote(settings: Settings, distance_m: int, weight_g: int) -> Decimal:
    amount = Decimal(str(settings.price_base)) \
        + Decimal(str(settings.price_per_km)) * Decimal(distance_m) / Decimal(1000) \
        + Decimal(str(settings.price_per_kg)) * Decimal(weight_g) / Decimal(1000)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def route_range_m(route: Route) -> int:
    """Требуемая дальность дрона: А→Б→В и возврат на базу; для маршрутов без базы — А→Б и обратно."""
    if route.base_lat is not None and route.return_m is not None:
        return route.distance_m + route.return_m
    return route.distance_m * 2


def route_points(route: Route) -> List[Tuple[float, float]]:
    """Опорные точки маршрута по порядку: [А (база)], Б (забор груза), [промежуточные], В (получатель)."""
    pts = [(route.origin_lat, route.origin_lon), *[(s["lat"], s["lon"]) for s in (route.stops or [])],
           (route.dest_lat, route.dest_lon)]
    if route.base_lat is not None:
        pts.insert(0, (route.base_lat, route.base_lon))
    return pts


def validate_order(db: Session, settings: Settings, order: Order, drone: Drone) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def check(code: str, ok: bool, message: str) -> None:
        checks.append({"code": code, "ok": bool(ok), "message": message})

    r = order.route
    from . import catalogs
    items = cargo_items(db, order)
    types = {c.code: c for c in catalogs.cargo_types(db)}
    unknown = [i.cargo_type for i in items if i.cargo_type not in types]
    check("cargo_type", not unknown, "Типы грузов разрешены" if not unknown
          else "Тип груза не разрешён (нет в справочнике «Типы грузов»)")
    text_blob = " ".join(f"{i.cargo_type} {i.description}" for i in items).lower()
    bad = [k for k in settings.prohibited_keywords if k.lower() in text_blob]
    check("cargo_prohibited", not bad, "Запрещённые категории не обнаружены" if not bad
          else "Описание груза содержит признаки запрещённой категории")
    heavy = [f"«{types[i.cargo_type].name}» — не более {types[i.cargo_type].max_weight_g} г" for i in items
             if i.cargo_type in types and i.weight_g > types[i.cargo_type].max_weight_g]
    heavy += [f"не более {settings.max_cargo_weight_g} г на один груз" for i in items
              if i.weight_g > settings.max_cargo_weight_g][:1]
    check("cargo_weight", not heavy, "Масса каждого груза не превышает допустимую для его типа" if not heavy
          else "Масса груза превышает допустимую: " + "; ".join(heavy))
    max_dim = max(max(i.length_mm, i.width_mm, i.height_mm) for i in items)
    check("cargo_dimensions", max_dim <= settings.max_cargo_dim_mm,
          f"Габариты не более {settings.max_cargo_dim_mm} мм по любой стороне")
    points = route_points(r)
    from . import zones
    fleet = db.get(Fleet, drone.fleet_id) if drone.fleet_id else None
    zone = zones.order_zone(db, settings, fleet) if fleet else zones.any_zone_contains_all(db, settings, points)
    in_zone = zone is not None and all(zones.contains(zone, lat, lon) for lat, lon in points)
    check("service_area", in_zone,
          f"Все точки маршрута в зоне обслуживания «{zone.name}»" if in_zone
          else (f"Точки маршрута вне зоны обслуживания «{zone.name}» парка дрона" if zone
                else "Парк дрона не входит ни в одну зону обслуживания"))
    check("distance", 0 < r.distance_m <= settings.max_distance_m,
          f"Расстояние маршрута {r.distance_m} м (допустимо до {settings.max_distance_m} м, точки не совпадают)")
    violated = []
    for z in settings.no_fly_zones:
        d = min(geo.segment_point_distance_m(a, b, (z.lat, z.lon)) for a, b in zip(points, points[1:]))
        if d < z.radius_m:
            violated.append(z.name)
    check("airspace", not violated, "Маршрут не пересекает зоны ограничения полётов" if not violated
          else "Маршрут пересекает зоны ограничения: " + ", ".join(violated))
    now = clock.now()
    window_ok = order.window_start > now and \
        (order.window_end - order.window_start) >= dt.timedelta(minutes=settings.min_window_minutes)
    if order.asap:
        check("time_window", True, "Время доставки не задано — доставка как можно скорее")
    else:
        check("time_window", window_ok,
              f"Временное окно в будущем и не короче {settings.min_window_minutes} мин")
    ok_drone, reasons = drones.readiness(db, settings, drone,
                                         distance_m=route_range_m(r), require_online=False)
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
    fleet = db.get(Fleet, drone.fleet_id) if drone is not None and drone.fleet_id else None
    # Дрон из открытого парка; дрон, зарегистрированный заказчиком до появления парков, — только свой.
    fleet_ok = fleet is not None and fleet.active and drone.published
    legacy_ok = drone is not None and fleet is None and drone.owner_id == p.user_id
    if drone is None or not (fleet_ok or legacy_ok):
        raise ValidationFailed("Выберите дрон из открытых для заказов парков", code="drone_invalid")
    def point(x: Dict[str, Any]) -> Dict[str, Any]:
        return {"lat": round(x["lat"], 6), "lon": round(x["lon"], 6), "address": x["address"].strip(),
                "note": (x.get("note") or "").strip()}
    pickup_at_base = bool(data.get("points"))
    if pickup_at_base:
        # Груз забирается на базе парка (А); точки Б, В, … — по порядку, последняя — грузополучатель.
        if fleet is None:
            raise ValidationFailed("Маршрут по точкам доступен только для дрона из парка", code="drone_invalid")
        pts = [point(x) for x in data["points"]]
        o = {"lat": fleet.base_lat, "lon": fleet.base_lon, "address": fleet.base_address or "База парка"}
        stops, d = pts[:-1], pts[-1]
    else:
        o, d = data["origin"], data["destination"]
        stops = [point(x) for x in (data.get("stops") or [])]
    chain = [(o["lat"], o["lon"]), *[(x["lat"], x["lon"]) for x in stops], (d["lat"], d["lon"])]
    leg_bc = sum(geo.haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(chain, chain[1:]))
    if fleet is not None:
        base = {"lat": fleet.base_lat, "lon": fleet.base_lon, "address": fleet.base_address}
        leg_ab = geo.haversine_m(base["lat"], base["lon"], o["lat"], o["lon"])
        return_m = int(round(geo.haversine_m(d["lat"], d["lon"], base["lat"], base["lon"])))
    else:
        base, leg_ab, return_m = None, 0.0, None
    distance = int(round(leg_ab + leg_bc))
    provider_id = data.get("map_provider_id")
    if provider_id is not None:
        from . import map_providers
        try:
            map_providers.get_active(db, provider_id)
        except NotFound as exc:
            raise ValidationFailed("Выбранная карта недоступна — выберите другую", code="map_provider_invalid") from exc
    items = normalize_cargo(db, data)
    consignee, priority_level, recipient = None, "normal", data.get("recipient_name")
    if data.get("consignee_id"):
        from . import catalogs
        try:
            c = catalogs.get_consignee(db, data["consignee_id"])
        except NotFound as exc:
            raise ValidationFailed("Выберите грузополучателя из списка", code="consignee_invalid") from exc
        consignee, priority_level, recipient = catalogs.snapshot(db, c), c.priority, recipient or c.name
    asap = data.get("window_start") is None
    now = clock.now()
    window_start = now if asap else data["window_start"]
    window_end = now + dt.timedelta(hours=ASAP_WINDOW_H) if asap else data["window_end"]
    order = Order(customer_id=p.user_id, drone_id=drone.id, status="draft", map_provider_id=provider_id,
                  **cargo_summary(items), recipient_name=recipient, recipient_phone=data.get("recipient_phone"),
                  window_start=window_start, window_end=window_end, asap=asap,
                  consignee_id=data.get("consignee_id"), consignee=consignee, priority_level=priority_level,
                  customer_comment=(data.get("customer_comment") or "").strip() or None,
                  created_at=now, updated_at=now)
    db.add(order)
    db.flush()
    save_cargo_items(db, order, items)
    db.add(Route(order_id=order.id, origin_lat=o["lat"], origin_lon=o["lon"], origin_address=o["address"],
                 dest_lat=d["lat"], dest_lon=d["lon"], dest_address=d["address"], distance_m=distance,
                 checkpoints=build_checkpoints(None if pickup_at_base else base, o, d, stops, letters=pickup_at_base),
                 stops=stops, pickup_at_base=pickup_at_base,
                 base_lat=base["lat"] if base else None, base_lon=base["lon"] if base else None,
                 base_address=base["address"] if base else None, return_m=return_m))
    db.add(OrderStatusHistory(order_id=order.id, from_status=None, to_status="draft", actor_id=p.user_id,
                              actor_kind="user", reason="Создан черновик", created_at=clock.now()))
    db.flush()
    db.refresh(order)
    audit.record(db, p.actor, "order.created", entity_type="order", entity_id=order.id,
                 details={"drone_id": drone.id, "distance_m": distance, "stops": len(stops)})
    return order


CARGO_FIELDS = ("cargo_type", "description", "weight_g", "length_mm", "width_mm", "height_mm")


def normalize_cargo(db: Session, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Грузы заказа: список cargo_items либо (по-старому) один груз из полей cargo_*."""
    items = data.get("cargo_items") or []
    if not items:
        if not data.get("cargo_type"):
            raise ValidationFailed("Добавьте хотя бы один груз", code="cargo_required")
        items = [{"cargo_type": data["cargo_type"], "description": data["cargo_description"],
                  "weight_g": data["cargo_weight_g"], "length_mm": data["cargo_length_mm"],
                  "width_mm": data["cargo_width_mm"], "height_mm": data["cargo_height_mm"]}]
    from . import catalogs
    out = []
    for it in items:
        c = catalogs.cargo_by_code(db, it["cargo_type"])
        if c is None or c.deleted_at is not None:
            raise ValidationFailed("Выберите тип груза из списка", code="cargo_type_invalid")
        out.append({f: (it[f].strip() if isinstance(it[f], str) else it[f]) for f in CARGO_FIELDS})
    return out


def cargo_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка в полях заказа: масса — сумма, габариты — наибольшие, тип — первого груза."""
    first = items[0]
    desc = first["description"] if len(items) == 1 else \
        "; ".join(f"{n}) {i['description']}" for n, i in enumerate(items, start=1))
    return {"cargo_type": first["cargo_type"], "cargo_description": desc[:1000],
            "cargo_weight_g": sum(i["weight_g"] for i in items),
            "cargo_length_mm": max(i["length_mm"] for i in items),
            "cargo_width_mm": max(i["width_mm"] for i in items),
            "cargo_height_mm": max(i["height_mm"] for i in items)}


def save_cargo_items(db: Session, order: Order, items: List[Dict[str, Any]]) -> None:
    for n, it in enumerate(items, start=1):
        db.add(OrderCargoItem(order_id=order.id, position=n, **{f: it[f] for f in CARGO_FIELDS}))
    db.flush()


def cargo_items(db: Session, order: Order) -> List[OrderCargoItem]:
    rows = list(db.execute(select(OrderCargoItem).where(OrderCargoItem.order_id == order.id)
                           .order_by(OrderCargoItem.position)).scalars())
    if rows:
        return rows
    # заказ без списка грузов — один груз из сводных полей
    return [OrderCargoItem(order_id=order.id, position=1, cargo_type=order.cargo_type,
                           description=order.cargo_description, weight_g=order.cargo_weight_g,
                           length_mm=order.cargo_length_mm, width_mm=order.cargo_width_mm,
                           height_mm=order.cargo_height_mm)]


def point_name(i: int) -> str:
    """Название точки маршрута заказчика: 0 → «Точка Б», 1 → «Точка В», …"""
    return "Точка " + POINT_LETTERS[i] if i < len(POINT_LETTERS) else f"Точка {i + 1}"


def build_checkpoints(base: Optional[Dict[str, Any]], o: Dict[str, Any], d: Dict[str, Any],
                      stops: Optional[List[Dict[str, Any]]] = None, *, letters: bool = False) -> List[Dict[str, Any]]:
    """Контрольные точки: [подлёт А→Б, Б — забор груза (kind=pickup)], затем Б→В через промежуточные
    точки заказчика (kind=stop). Справочные точки маршрута для отображения и учёта прохождения,
    не команды дрону."""
    def pt(name: str, lat: float, lon: float, kind: str = "checkpoint") -> Dict[str, Any]:
        return {"name": name, "lat": round(lat, 6), "lon": round(lon, 6), "kind": kind}
    cps: List[Dict[str, Any]] = []
    if base is not None:
        lat, lon = geo.interpolate((base["lat"], base["lon"]), (o["lat"], o["lon"]), 0.5)
        cps.append(pt("Подлёт", lat, lon))
        cps.append(pt("Б — забор груза", o["lat"], o["lon"], "pickup"))
    if not stops:
        for i, t in enumerate((0.25, 0.5, 0.75), start=1):
            lat, lon = geo.interpolate((o["lat"], o["lon"]), (d["lat"], d["lon"]), t)
            cps.append(pt(f"КТ-{i}", lat, lon))
        return cps
    # с промежуточными точками — контрольная точка посередине каждого участка
    chain = [o, *stops, d]
    n = 0
    for i, (a, b) in enumerate(zip(chain, chain[1:])):
        n += 1
        lat, lon = geo.interpolate((a["lat"], a["lon"]), (b["lat"], b["lon"]), 0.5)
        cps.append(pt(f"КТ-{n}", lat, lon))
        if i < len(stops):
            s = stops[i]
            cps.append(pt(f"{point_name(i) if letters else i + 1} — {s['address']}", s["lat"], s["lon"], "stop"))
    return cps


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
    ok, reasons = drones.readiness(db, settings, drone, require_online=False)
    if not ok:
        raise ValidationFailed("Дрон больше не допущен к выполнению: " + "; ".join(reasons), code="drone_not_ready")
    transition(db, order, "awaiting_coordinator", p.actor, reason="Заказ размещён, ждёт согласования координатором")
    _post_customer_comment(db, order)
    # Согласующий координатор — с наименьшей нагрузкой (services/coordination.py).
    from . import coordination
    from .. import runtime
    coordination.auto_assign(db, runtime.settings(), order)
    return order


def _post_customer_comment(db: Session, order: Order) -> None:
    """Комментарий заказчика (и фото) при размещении заказа попадает в переписку по заказу."""
    if not (order.customer_comment or order.comment_photo_key):
        return
    from .. import runtime
    note = OrderNote(order_id=order.id, author_id=order.customer_id, kind="chat", severity="info",
                     body=order.customer_comment or "Фото к заказу", created_at=clock.now(),
                     attachment_key=order.comment_photo_key, attachment_type=order.comment_photo_type,
                     attachment_sha256=runtime.store().sha256(order.comment_photo_key) if order.comment_photo_key else None)
    db.add(note)
    db.flush()


COMMENT_PHOTO_MAX = 15 * 1024 * 1024


def set_comment_photo(db: Session, p: Principal, order_id: uuid.UUID, data: bytes) -> Order:
    """Фото к комментарию заказчика (до размещения заказа)."""
    from . import support
    from .. import runtime
    order = visible_order(db, p, order_id, for_update=True)
    if order.customer_id != p.user_id:
        raise Forbidden("Фото к заказу прикладывает заказчик")
    if order.status not in ("draft", "pending_confirmation"):
        raise Conflict("Фото можно приложить до размещения заказа", code="invalid_transition")
    kind = support.sniff_media(data[:64])
    if not kind.startswith("image/"):
        raise ValidationFailed("Приложите фото (PNG, JPEG или WebP)", code="attachment_type")
    if len(data) > COMMENT_PHOTO_MAX:
        raise ValidationFailed("Фото больше 15 МБ", code="attachment_too_large")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(kind, "img")
    key = f"chat/{order.id}/comment-{uuid.uuid4().hex[:12]}.{ext}"
    import os
    import tempfile
    from pathlib import Path
    fd, tmp = tempfile.mkstemp(prefix="fpv-order-photo-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        runtime.store().put_file(key, Path(tmp), kind)
    finally:
        os.unlink(tmp)
    order.comment_photo_key, order.comment_photo_type = key, kind
    order.updated_at = clock.now()
    audit.record(db, p.actor, "order.comment_photo", entity_type="order", entity_id=order.id,
                 details={"size": len(data), "type": kind})
    return order


def coordinator_decide(db: Session, p: Principal, order_id: uuid.UUID, approve: bool, comment: str = "") -> Order:
    """Координатор согласует новый заказ (и закрепляется за ним) или отклоняет его с причиной."""
    p.require("order.approve")
    order = visible_order(db, p, order_id, for_update=True)
    if order.status != "awaiting_coordinator":
        raise Conflict("Заказ не ждёт согласования координатором", code="invalid_transition")
    if order.coordinator_id not in (None, p.user_id) and not p.has("order.assign"):
        raise Forbidden("Заказ согласует назначенный ему координатор")
    comment = (comment or "").strip()
    if approve:
        if order.coordinator_id != p.user_id and "coordinator" in p.roles:
            for a in db.execute(select(Assignment).where(Assignment.order_id == order.id, Assignment.status == "active",
                                                          Assignment.role == "coordinator")).scalars():
                a.status, a.reason, a.ended_at = "released", "Заказ согласовал другой координатор", clock.now()
            db.flush()
            db.add(Assignment(order_id=order.id, user_id=p.user_id, role="coordinator", created_by=p.user_id,
                              created_at=clock.now()))
            order.coordinator_id = p.user_id
        order.coordinator_approved_at = clock.now()
        transition(db, order, "awaiting_operator", p.actor, reason=comment or "Заказ согласован координатором")
    else:
        if len(comment) < 3:
            raise ValidationFailed("Укажите причину отказа (не менее 3 символов)")
        order.closed_reason = comment
        _end_assignments(db, order, "released", comment)
        transition(db, order, "rejected", p.actor, reason=f"Координатор отклонил заказ: {comment}")
    audit.record(db, p.actor, "order.coordinator_approved" if approve else "order.coordinator_rejected",
                 entity_type="order", entity_id=order.id, details={"comment": comment})
    return order


def pending_approval(db: Session, p: Principal) -> List[Order]:
    """Заказы, ждущие согласования этим координатором (и без координатора); администратору — все."""
    p.require("order.approve" if not p.has("order.read_all") else "order.read_all")
    stmt = select(Order).where(Order.status == "awaiting_coordinator")
    if not p.has("order.read_all"):
        stmt = stmt.where(or_(Order.coordinator_id == p.user_id, Order.coordinator_id.is_(None)))
    return list(db.execute(stmt.order_by(PRIORITY_FIRST, Order.submitted_at)).unique().scalars())


def drone_chain(db: Session, orders: List[Order]) -> Dict[uuid.UUID, Optional[int]]:
    """Для заказов в очереди: номер заказа, который должен вылететь на этом дроне раньше (или None).
    Порядок вылета одного дрона — по времени поступления на вылет (согласования координатором)."""
    drones_ids = {o.drone_id for o in orders}
    if not drones_ids:
        return {}
    busy = dict(db.execute(select(Order.drone_id, Order.number).where(
        Order.drone_id.in_(drones_ids), Order.status.in_(ACTIVE_EXECUTION))).all())
    waiting = db.execute(select(Order.drone_id, Order.id, Order.number).where(
        Order.drone_id.in_(drones_ids), Order.status == "awaiting_operator")
        .order_by(Order.coordinator_approved_at.asc().nulls_last(), Order.submitted_at, Order.number)).all()
    first: Dict[uuid.UUID, Tuple[uuid.UUID, int]] = {}
    for drone_id, oid, number in waiting:
        first.setdefault(drone_id, (oid, number))
    out: Dict[uuid.UUID, Optional[int]] = {}
    for o in orders:
        if o.drone_id in busy:
            out[o.id] = busy[o.drone_id]
        else:
            head = first.get(o.drone_id)
            out[o.id] = head[1] if head and head[0] != o.id else None
    return out


def cancel(db: Session, p: Principal, order_id: uuid.UUID, reason: str,
           expected_version: Optional[int] = None) -> Order:
    order = visible_order(db, p, order_id, for_update=True)
    _check_version(order, expected_version)
    is_owner = order.customer_id == p.user_id and p.has("order.cancel_own")
    is_admin = p.has("order.assign")
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
    # Заказы, которые система предложила этому оператору (services/operator_dispatch.py).
    stmt = select(Order).join(Route, Route.order_id == Order.id).where(
        Order.status == "awaiting_operator", Order.offered_operator_id == p.user_id)
    if max_distance_m:
        stmt = stmt.where(Route.distance_m <= max_distance_m)
    if cargo_type:
        stmt = stmt.where(Order.cargo_type == cargo_type)
    return list(db.execute(stmt.order_by(PRIORITY_FIRST, Order.window_start)).unique().scalars())


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
    return list(db.execute(stmt.order_by(PRIORITY_FIRST, Order.created_at.desc()).limit(500)).unique().scalars())


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
    from . import readiness
    if not readiness.is_ready(db, p.user_id, "operator"):
        raise Conflict("Вы отметили «Не готов к работе» — чтобы принимать заказы, отметьте «Готов к работе»",
                       code="not_ready_for_work")
    if order.offered_operator_id != p.user_id:
        raise Conflict("Заказ предложен другому оператору", code="not_offered")
    busy = db.execute(select(Order.id).where(Order.operator_id == p.user_id,
                                             Order.status.in_(ACTIVE_EXECUTION))).first()
    if busy:
        raise Conflict("У оператора уже есть активное задание", code="operator_busy")
    from . import licenses
    licenses.check_admitted(db, p.user_id, drones.get_drone(db, order.drone_id))
    before = drone_chain(db, [order]).get(order.id)
    if before is not None:
        raise Conflict(f"Сначала на этом дроне вылетает заказ №{before} (поступил на вылет раньше)",
                       code="drone_queue")
    order.operator_id = p.user_id
    order.accepted_at = clock.now()
    db.add(Assignment(order_id=order.id, user_id=p.user_id, role="operator", created_by=p.user_id,
                      created_at=clock.now()))
    db.flush()
    transition(db, order, "accepted", p.actor, reason="Заказ принят оператором")
    # Координатор назначается автоматически — с наименьшей нагрузкой (services/coordination.py).
    from . import coordination
    from .. import runtime
    coordination.auto_assign(db, runtime.settings(), order)
    return order


def _mark_declined(db: Session, p: Principal, order: Order, reason: str) -> None:
    exists = db.execute(select(Assignment.id).where(
        Assignment.order_id == order.id, Assignment.user_id == p.user_id, Assignment.role == "operator",
        Assignment.status == "declined")).first()
    if not exists:
        db.add(Assignment(order_id=order.id, user_id=p.user_id, role="operator", status="declined",
                          reason=reason, created_by=p.user_id, created_at=clock.now(), ended_at=clock.now()))
        db.flush()


def decline(db: Session, p: Principal, order_id: uuid.UUID, reason: str,
            expected_version: Optional[int] = None) -> Order:
    p.require("order.accept")
    order = visible_order(db, p, order_id, for_update=True)
    _check_version(order, expected_version)
    if order.status == "awaiting_operator":
        if order.offered_operator_id != p.user_id:
            raise Conflict("Заказ предложен другому оператору", code="not_offered")
        _mark_declined(db, p, order, reason)
        audit.record(db, p.actor, "order.declined", entity_type="order", entity_id=order.id,
                     details={"reason": reason})
        from . import operator_dispatch
        operator_dispatch.offer(db, order, "Предыдущий оператор отклонил заказ")   # другому подходящему
        return order
    if order.status in ("accepted", "awaiting_readiness", "ready"):
        _require_own_operator(p, order)
        _end_assignments(db, order, "released", reason or "Оператор отказался от задания", role="operator")
        _mark_declined(db, p, order, reason or "Оператор отказался от задания")   # больше ему не предлагать
        order.operator_id = None
        transition(db, order, "awaiting_operator", p.actor, reason=reason or "Оператор отказался от задания")
        return order
    raise Conflict("Отказ от заказа в текущем статусе невозможен", code="invalid_transition")


def operator_readiness(db: Session, settings: Settings, order: Order, drone: Drone) -> Tuple[bool, List[str]]:
    """Готовность к началу задания: допуск дрона и подтверждение техника парка."""
    ok, reasons = drones.readiness(db, settings, drone,
                                   distance_m=route_range_m(order.route))
    if order.tech_ready_at is None:
        reasons.append("Техник парка ещё не подтвердил готовность дрона к этому заказу"
                       if drone.fleet_id else
                       "Дрон не входит в парк: администратор должен перевести его в парк техника")
    return (not reasons), reasons


def check_readiness(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID) -> Tuple[Order, List[str]]:
    p.require("order.execute")
    order = visible_order(db, p, order_id, for_update=True)
    _require_own_operator(p, order)
    if order.status not in ("accepted", "awaiting_readiness", "ready"):
        raise Conflict("Проверка готовности недоступна в текущем статусе", code="invalid_transition")
    drone = drones.get_drone(db, order.drone_id)
    ok, reasons = operator_readiness(db, settings, order, drone)
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
    ok, reasons = operator_readiness(db, settings, order, drone)
    if not ok:
        transition(db, order, "awaiting_readiness", audit.Actor.system("readiness-check"), actor_kind="system",
                   reason="; ".join(reasons))
        return order
    order.started_at = clock.now()
    transition(db, order, "in_progress", p.actor, reason="Задание начато оператором")
    # Проверка видео техником заканчивается: дальше видео идёт в буфер заказа.
    from . import fleets
    drone.video_check_until = None
    fleets.remove_check_buffer(settings, drone.id)
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
    """Проверка и оценка выполнения координатором заказа: принятие/отклонение отчёта, проверка видео
    и окончательное начисление баллов. (Статус по-прежнему называется admin_review.)"""
    from . import scoring, video
    p.require("order.review")
    order = get_order(db, order_id, for_update=True)
    _require_reviewer(p, order)
    _check_version(order, expected_version)
    if order.status != "admin_review":
        raise Conflict("Заказ не находится на проверке выполнения", code="invalid_transition")
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
        transition(db, order, "completed", p.actor, reason=comment or "Выполнение подтверждено координатором")
    else:
        order.closed_reason = comment
        transition(db, order, "rejected", p.actor, reason=comment)
    scoring.finalize(db, p, order)
    return order


def _require_reviewer(p: Principal, order: Order) -> None:
    """Проверяет выполнение координатор этого заказа (заказ без координатора — любой координатор)."""
    if order.coordinator_id is not None and order.coordinator_id != p.user_id:
        raise Forbidden("Выполнение проверяет координатор этого заказа")


def reviews_for(db: Session, p: Principal) -> List[Order]:
    """Заказы на проверке выполнения у этого координатора."""
    p.require("order.review")
    return list(db.execute(select(Order).where(
        Order.status == "admin_review", or_(Order.coordinator_id == p.user_id, Order.coordinator_id.is_(None)))
        .order_by(PRIORITY_FIRST, Order.delivered_at)).unique().scalars())


def history(db: Session, order_id: uuid.UUID) -> List[OrderStatusHistory]:
    return list(db.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
                           .order_by(OrderStatusHistory.id)).scalars())
