"""Парки дронов: техник как администратор своего парка.

Порядок работы:

1. Техник создаёт парк с базой (точка А маршрута) и заводит в него дроны; регистрацию дрона,
   как и раньше, подтверждает администратор.
2. Техник проводит осмотр, выпускает ключ сервисного модуля и проверяет подключение:
   связь, телеметрию и видео (пробная трансляция до заказа, источник — USB/RTSP/ролик).
3. Техник открывает дрон для заказов (``published``); заказчик выбирает дрон из открытых.
4. По заказу на дроне своего парка техник подтверждает первичную готовность дрона к полёту
   (``order.tech_ready_at``). Без неё оператор не пройдёт проверку готовности.

Ничего из этого не управляет полётом: проверка видео и выбор источника касаются только видео.
"""
from __future__ import annotations

import datetime as dt
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Drone, Fleet, Order, TelemetryRecord
from . import audit, drones, events, notifications, video, video_sources
from .identity import Principal

CHECK_SOURCE = "check"            # буфер пробной трансляции: <spool>/<drone_id>/check/ring
CHECK_MINUTES = 5                 # сколько длится пробная трансляция после нажатия «Проверить видео»
CHECK_KEEP_S = 30
CHECK_VALID_H = 12                # проверка видео действительна для подтверждения готовности
TELEMETRY_FRESH_S = 30
PREP_STATUSES = ("awaiting_operator", "accepted", "awaiting_readiness", "ready")
FLEET_ORDER_STATUSES = ("awaiting_operator", "accepted", "awaiting_readiness", "ready",
                        "in_progress", "awaiting_delivery_confirmation")


# --- парки ---------------------------------------------------------------------------------------

def get_fleet(db: Session, fleet_id: uuid.UUID) -> Fleet:
    fleet = db.get(Fleet, fleet_id)
    if fleet is None:
        raise NotFound("Парк не найден")
    return fleet


def own_fleet(db: Session, p: Principal, fleet_id: uuid.UUID) -> Fleet:
    p.require("fleet.manage")
    fleet = get_fleet(db, fleet_id)
    if fleet.technician_id != p.user_id:
        raise NotFound("Парк не найден")
    return fleet


def list_fleets(db: Session, p: Principal) -> List[Fleet]:
    stmt = select(Fleet).order_by(Fleet.created_at)
    if p.has("drone.read_all") and not p.has("fleet.manage"):
        return list(db.execute(stmt).scalars())        # администратор — все парки
    p.require("fleet.manage")
    return list(db.execute(stmt.where(Fleet.technician_id == p.user_id)).scalars())


def _check_base(db: Session, technician_id: uuid.UUID, b: Dict[str, Any]):
    """База — в зоне обслуживания, доступной технику; возвращает зону для привязки парка."""
    from .. import runtime
    from . import zones
    return zones.check_fleet_base(db, runtime.settings(), technician_id, b["lat"], b["lon"])


def create_fleet(db: Session, p: Principal, data: Dict[str, Any]) -> Fleet:
    p.require("fleet.manage")
    b = data["base"]
    zone = _check_base(db, p.user_id, b)
    fleet = Fleet(name=data["name"], technician_id=p.user_id, base_lat=b["lat"], base_lon=b["lon"],
                  base_address=b["address"], zone_id=zone.id, created_at=clock.now(), updated_at=clock.now())
    db.add(fleet)
    db.flush()
    audit.record(db, p.actor, "fleet.created", entity_type="fleet", entity_id=fleet.id,
                 details={"name": fleet.name, "base": [fleet.base_lat, fleet.base_lon], "zone": zone.name})
    return fleet


def update_fleet(db: Session, p: Principal, fleet_id: uuid.UUID, data: Dict[str, Any]) -> Fleet:
    fleet = own_fleet(db, p, fleet_id)
    if data.get("name"):
        fleet.name = data["name"]
    if data.get("base"):
        b = data["base"]
        zone = _check_base(db, fleet.technician_id, b)
        fleet.base_lat, fleet.base_lon, fleet.base_address = b["lat"], b["lon"], b["address"]
        fleet.zone_id = zone.id
    if data.get("active") is not None:
        fleet.active = bool(data["active"])
    fleet.updated_at = clock.now()
    audit.record(db, p.actor, "fleet.updated", entity_type="fleet", entity_id=fleet.id,
                 details={k: v for k, v in data.items() if v is not None and k != "base"})
    return fleet


def fleet_technician(db: Session, drone: Drone) -> Optional[uuid.UUID]:
    if drone.fleet_id is None:
        return None
    fleet = db.get(Fleet, drone.fleet_id)
    return fleet.technician_id if fleet else None


def fleet_drone(db: Session, p: Principal, drone_id: uuid.UUID, *, for_update: bool = False) -> Drone:
    """Дрон своего парка (техник)."""
    p.require("fleet.manage")
    drone = drones.get_drone(db, drone_id, for_update=for_update)
    if fleet_technician(db, drone) != p.user_id:
        raise NotFound("Дрон не найден в вашем парке")
    return drone


def covers_drone(db: Session, user_id: uuid.UUID, drone_id: uuid.UUID) -> bool:
    """Техник ведёт заказ на этом дроне вместо неготового техника парка (services/tech_cover.py)."""
    return db.execute(select(Order.id).where(Order.drone_id == drone_id, Order.duty_technician_id == user_id,
                                             Order.status.in_(FLEET_ORDER_STATUSES)).limit(1)).first() is not None


def served_drone(db: Session, p: Principal, drone_id: uuid.UUID, *, for_update: bool = False) -> Drone:
    """Дрон, с которым техник работает по заказу: своего парка или парка, который он замещает."""
    p.require("fleet.manage")
    drone = drones.get_drone(db, drone_id, for_update=for_update)
    if fleet_technician(db, drone) != p.user_id and not covers_drone(db, p.user_id, drone.id):
        raise NotFound("Дрон не найден в вашем парке")
    return drone


def _serves(db: Session, p: Principal, order: Order, drone: Drone) -> bool:
    return p.user_id in (fleet_technician(db, drone), order.duty_technician_id)


def assign_to_fleet(db: Session, p: Principal, drone_id: uuid.UUID, fleet_id: uuid.UUID) -> Drone:
    """Администратор переводит дрон (например, зарегистрированный заказчиком до появления парков) в парк."""
    p.require("drone.approve")
    drone = drones.get_drone(db, drone_id, for_update=True)
    fleet = get_fleet(db, fleet_id)
    before = drone.fleet_id
    drone.fleet_id = fleet.id
    drone.published = False
    drone.updated_at = clock.now()
    audit.record(db, p.actor, "drone.fleet_assigned", entity_type="drone", entity_id=drone.id,
                 details={"from": str(before) if before else None, "to": str(fleet.id)})
    notifications.create(db, [fleet.technician_id], "drone.fleet",
                         f"Дрон {drone.registration_number} переведён в ваш парк «{fleet.name}»")
    return drone


def set_published(db: Session, p: Principal, drone_id: uuid.UUID, published: bool) -> Drone:
    drone = fleet_drone(db, p, drone_id, for_update=True)
    if published:
        if drone.archived_at is not None:
            raise Conflict("Дрон в архиве БПЛА — сначала верните его в парк", code="drone_archived")
        problems = []
        if drone.status != "active":
            problems.append("регистрация не подтверждена администратором")
        if drone.health_status != "serviceable":
            problems.append("дрон не признан исправным по осмотру")
        if drone.api_key_hash is None:
            problems.append("не выпущен ключ сервисного модуля")
        if problems:
            raise Conflict("Открыть дрон для заказов нельзя: " + "; ".join(problems), code="drone_not_publishable")
    drone.published = published
    drone.updated_at = clock.now()
    audit.record(db, p.actor, "drone.published" if published else "drone.unpublished",
                 entity_type="drone", entity_id=drone.id)
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[p.user_id], roles=["admin"])
    return drone


def available_drones(db: Session, p: Principal) -> List[Tuple[Drone, Fleet, bool]]:
    """Дроны, открытые для заказов (для заказчика): с парком и признаком занятости."""
    p.require("order.create")
    from .orders import ACTIVE_EXECUTION
    rows = db.execute(select(Drone, Fleet).join(Fleet, Fleet.id == Drone.fleet_id)
                      .where(Drone.published.is_(True), Drone.status == "active", Drone.archived_at.is_(None),
                             Drone.health_status == "serviceable", Fleet.active.is_(True))
                      .order_by(Fleet.name, Drone.registration_number)).all()
    busy = {d for (d,) in db.execute(select(Order.drone_id).where(Order.status.in_(ACTIVE_EXECUTION))).all()}
    return [(d, f, d.id in busy) for d, f in rows]


# --- архив БПЛА ----------------------------------------------------------------------------------

ARCHIVE_REASONS = {
    "broken": "Сломан", "lost": "Утерян", "repair": "В ремонте", "returned": "Возвращён производителю",
    "worn_out": "Отработал", "dismantled": "Разобран", "obsolete": "Устарел",
    "peripherals": "Поломка/отсутствие периферийного оборудования", "other": "Другое",
}


def archive_drone(db: Session, p: Principal, drone_id: uuid.UUID, reason: str, comment: str = "") -> Drone:
    """Техник переносит дрон своего парка в архив: дрон пропадает из «Мой парк» и из выбора заказчика."""
    p.require("drone.archive")
    drone = fleet_drone(db, p, drone_id, for_update=True)
    if drone.archived_at is not None:
        raise Conflict("Дрон уже в архиве", code="drone_archived")
    if reason not in ARCHIVE_REASONS:
        raise ValidationFailed("Выберите причину переноса в архив", code="archive_reason")
    comment = (comment or "").strip()
    if reason == "other" and len(comment) < 3:
        raise ValidationFailed("Для причины «Другое» напишите комментарий", code="archive_comment_required")
    busy = db.execute(select(Order.number).where(Order.drone_id == drone.id,
                                                 Order.status.in_(FLEET_ORDER_STATUSES)).limit(1)).first()
    if busy:
        raise Conflict(f"Дрон назначен на незавершённый заказ №{busy[0]} — перенести в архив нельзя",
                       code="drone_busy")
    drone.archived_at = clock.now()
    drone.archived_by = p.user_id
    drone.archive_reason = reason
    drone.archive_comment = comment or None
    drone.published = False
    drone.updated_at = clock.now()
    audit.record(db, p.actor, "drone.archived", entity_type="drone", entity_id=drone.id,
                 details={"reason": reason, "comment": comment})
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[p.user_id], roles=["admin"])
    return drone


def unarchive_drone(db: Session, p: Principal, drone_id: uuid.UUID) -> Drone:
    """Возврат из архива в парк. Дрон снова требует осмотра техником, прежде чем открыть его для заказов."""
    p.require("drone.archive")
    drone = fleet_drone(db, p, drone_id, for_update=True)
    if drone.archived_at is None:
        raise Conflict("Дрон не в архиве", code="drone_not_archived")
    before = drone.archive_reason
    drone.archived_at = drone.archived_by = drone.archive_reason = drone.archive_comment = None
    drone.health_status = "requires_check"
    drone.updated_at = clock.now()
    audit.record(db, p.actor, "drone.unarchived", entity_type="drone", entity_id=drone.id,
                 details={"reason_was": before})
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[p.user_id], roles=["admin"])
    return drone


def technician_drones(db: Session, p: Principal, *, archived: Optional[bool] = None) -> List[Tuple[Drone, Fleet]]:
    p.require("fleet.manage")
    stmt = select(Drone, Fleet).join(Fleet, Fleet.id == Drone.fleet_id).where(Fleet.technician_id == p.user_id)
    if archived is True:
        stmt = stmt.where(Drone.archived_at.is_not(None)).order_by(Drone.archived_at.desc())
    elif archived is False:
        stmt = stmt.where(Drone.archived_at.is_(None))
    return list(db.execute(stmt.order_by(Fleet.name, Drone.registration_number)).all())


def last_flights(db: Session, drone_ids: List[uuid.UUID]) -> Dict[uuid.UUID, dt.datetime]:
    if not drone_ids:
        return {}
    from sqlalchemy import func
    return dict(db.execute(select(Order.drone_id, func.max(Order.started_at))
                           .where(Order.drone_id.in_(drone_ids), Order.started_at.is_not(None))
                           .group_by(Order.drone_id)).all())


# --- подключение и проверка видео -----------------------------------------------------------------

def last_telemetry(db: Session, drone: Drone) -> Optional[TelemetryRecord]:
    return db.execute(select(TelemetryRecord).where(TelemetryRecord.drone_id == drone.id)
                      .order_by(TelemetryRecord.recorded_at.desc()).limit(1)).scalar_one_or_none()


def check_active(drone: Drone, now: Optional[dt.datetime] = None) -> bool:
    return drone.video_check_until is not None and drone.video_check_until > (now or clock.now())


def connection(db: Session, settings: Settings, p: Principal, drone_id: uuid.UUID) -> Dict[str, Any]:
    drone = served_drone(db, p, drone_id)
    t = last_telemetry(db, drone)
    now = clock.now()
    buf = video.buffer_state(settings, drone.id, CHECK_SOURCE)
    return {
        "link_status": drones.compute_link_status(settings, drone, now),
        "last_seen_at": drone.last_seen_at.isoformat() if drone.last_seen_at else None,
        "battery_pct": drone.battery_pct,
        "telemetry": None if t is None else {
            "recorded_at": t.recorded_at.isoformat(), "age_s": round((now - t.recorded_at).total_seconds(), 1),
            "lat": t.lat, "lon": t.lon, "alt_m": t.alt_m, "battery_pct": t.battery_pct,
            "link_quality": t.link_quality, "gnss_fix": t.gnss_fix, "flight_phase": t.flight_phase},
        "video": {"sources": drone.video_sources or [], "active": drone.video_source_active,
                  "selected": drone.video_source_selected,
                  "check_active": check_active(drone, now),
                  "check_until": drone.video_check_until.isoformat() if check_active(drone, now) else None,
                  "checked_at": drone.video_checked_at.isoformat() if drone.video_checked_at else None,
                  "receiving": buf["last_segment_age_s"] is not None and buf["last_segment_age_s"] <= 6},
        "has_key": drone.api_key_hash is not None,
        "published": drone.published,
    }


def start_video_check(db: Session, p: Principal, drone_id: uuid.UUID) -> Drone:
    drone = served_drone(db, p, drone_id, for_update=True)
    if drone.api_key_hash is None:
        raise Conflict("Сначала выпустите ключ сервисного модуля", code="no_key")
    from .telemetry import current_order
    if current_order(db, drone) is not None:
        raise Conflict("Дрон выполняет заказ — видео смотрите в заказе", code="drone_busy")
    drone.video_check_until = clock.now() + dt.timedelta(minutes=CHECK_MINUTES)
    audit.record(db, p.actor, "drone.video_check.started", entity_type="drone", entity_id=drone.id)
    return drone


def stop_video_check(db: Session, settings: Settings, p: Principal, drone_id: uuid.UUID) -> Drone:
    drone = served_drone(db, p, drone_id, for_update=True)
    drone.video_check_until = None
    remove_check_buffer(settings, drone.id)
    return drone


def select_video_source(db: Session, p: Principal, drone_id: uuid.UUID, source_id: str) -> Drone:
    """Источник видео дрона по умолчанию (выбирает техник при подключении)."""
    drone = served_drone(db, p, drone_id, for_update=True)
    video_sources.apply_selection(db, p, drone, source_id, entity_type="drone", entity_id=drone.id)
    return drone


def accept_check_segment(db: Session, settings: Settings, drone: Drone, seq: int, duration_ms: int,
                         data: bytes, content_type: str) -> Dict[str, Any]:
    """Сегмент пробной трансляции (дрон не выполняет заказ, техник запустил проверку видео)."""
    if content_type not in video.ALLOWED_CONTENT_TYPES:
        raise ValidationFailed("Неподдерживаемый формат видеосегмента")
    if not data or not (100 <= duration_ms <= 10000) or seq < 0:
        raise ValidationFailed("Недопустимые параметры видеосегмента")
    result = video.write_segment(settings, drone.id, CHECK_SOURCE, seq, duration_ms, data, keep_s=CHECK_KEEP_S)
    if result["accepted"]:
        first = drone.video_checked_at is None or (clock.now() - drone.video_checked_at).total_seconds() > 60
        drone.video_checked_at = clock.now()
        if first:
            tech = fleet_technician(db, drone)
            events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[tech] if tech else [])
    return result


def remove_check_buffer(settings: Settings, drone_id: uuid.UUID) -> None:
    shutil.rmtree(video.ring_dir(settings, drone_id, CHECK_SOURCE).parent, ignore_errors=True)


def sweep_checks(db: Session, settings: Settings) -> int:
    """Удаление буферов пробных трансляций по окончании проверки."""
    root = Path(settings.video_spool_dir)
    removed = 0
    if not root.is_dir():
        return 0
    for d in root.iterdir():
        if not (d / CHECK_SOURCE).is_dir():
            continue
        try:
            drone = db.get(Drone, uuid.UUID(d.name))
        except ValueError:
            continue
        if drone is None or not check_active(drone):
            remove_check_buffer(settings, uuid.UUID(d.name))
            removed += 1
    return removed


# --- заказы на дронах парка и первичная готовность --------------------------------------------------

def fleet_orders(db: Session, p: Principal) -> List[Order]:
    p.require("order.read_fleet")
    from . import orders as orders_mod
    return list(db.execute(
        select(Order).join(Drone, Drone.id == Order.drone_id).join(Fleet, Fleet.id == Drone.fleet_id)
        .where(or_(Fleet.technician_id == p.user_id, Order.duty_technician_id == p.user_id),
               Order.status.in_(FLEET_ORDER_STATUSES))
        .order_by(orders_mod.PRIORITY_FIRST, Order.created_at.desc()).limit(200)).unique().scalars())


def readiness_reasons(db: Session, settings: Settings, order: Order, drone: Drone) -> List[str]:
    from .orders import route_range_m
    ok, reasons = drones.readiness(db, settings, drone,
                                   distance_m=route_range_m(order.route))
    t = last_telemetry(db, drone)
    if t is None or (clock.now() - t.recorded_at).total_seconds() > TELEMETRY_FRESH_S:
        reasons.append("Телеметрия от сервисного модуля не поступает")
    if drone.video_checked_at is None or \
            (clock.now() - drone.video_checked_at).total_seconds() > CHECK_VALID_H * 3600:
        reasons.append(f"Видео с дрона не проверено (нужна проверка видео за последние {CHECK_VALID_H} ч)")
    return reasons


def confirm_order_ready(db: Session, settings: Settings, p: Principal, order_id: uuid.UUID) -> Tuple[Order, List[str]]:
    """Техник парка подтверждает первичную готовность дрона к заказу."""
    from .orders import get_order
    p.require("fleet.manage")
    order = get_order(db, order_id, for_update=True)
    drone = drones.get_drone(db, order.drone_id)
    if not _serves(db, p, order, drone):
        raise NotFound("Заказ не найден")
    if order.status not in PREP_STATUSES:
        raise Conflict("Подтверждение готовности доступно до начала выполнения заказа", code="invalid_transition")
    reasons = readiness_reasons(db, settings, order, drone)
    audit.record(db, p.actor, "order.tech_ready_checked", entity_type="order", entity_id=order.id,
                 outcome="success" if not reasons else "denied", details={"reasons": reasons})
    if reasons:
        return order, reasons
    order.tech_ready_at = clock.now()
    order.tech_ready_by = p.user_id
    order.version += 1
    order.updated_at = clock.now()
    notifications.create(db, [order.operator_id, order.customer_id], "order.tech_ready",
                         f"Заказ №{order.number}: техник подтвердил готовность дрона {drone.registration_number}",
                         order_id=order.id)
    events.notify(db, "order.updated", {"order_id": order.id},
                  users=[order.customer_id, order.operator_id, order.coordinator_id, p.user_id], roles=["admin"])
    return order, []


def revoke_order_ready(db: Session, p: Principal, order_id: uuid.UUID, reason: str) -> Order:
    from .orders import get_order
    p.require("fleet.manage")
    order = get_order(db, order_id, for_update=True)
    drone = drones.get_drone(db, order.drone_id)
    if not _serves(db, p, order, drone):
        raise NotFound("Заказ не найден")
    if order.status not in PREP_STATUSES:
        raise Conflict("Отозвать подтверждение можно только до начала выполнения", code="invalid_transition")
    if not reason:
        raise ValidationFailed("Укажите причину")
    order.tech_ready_at = None
    order.tech_ready_by = None
    order.version += 1
    audit.record(db, p.actor, "order.tech_ready_revoked", entity_type="order", entity_id=order.id,
                 details={"reason": reason})
    notifications.create(db, [order.operator_id], "order.tech_ready",
                         f"Заказ №{order.number}: техник отозвал подтверждение готовности", reason,
                         order_id=order.id, severity="warning")
    events.notify(db, "order.updated", {"order_id": order.id},
                  users=[order.customer_id, order.operator_id, p.user_id], roles=["admin"])
    return order


def is_fleet_technician_of(db: Session, p: Principal, order: Order) -> bool:
    if not p.has("order.read_fleet"):
        return False
    drone = db.get(Drone, order.drone_id)
    return drone is not None and _serves(db, p, order, drone)
