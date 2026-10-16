"""Drone Registry Service: регистрация, допуск, техническое состояние, ключи подключения.

Правила допуска:
* новый дрон получает status='pending_approval' и health_status='requires_check';
* к заказам допускается только дрон со status='active' и health_status='serviceable';
* статус «Исправен» (serviceable) устанавливает только техник;
* автоматические отчёты устройства могут лишь понизить техническое состояние.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import Drone, DroneHealthCheck, MaintenanceRecord
from ..security.tokens import hash_token, new_drone_key
from . import audit, events, notifications
from .identity import Principal

HEALTH_STATUSES = ("serviceable", "requires_check", "grounded")


def get_drone(db: Session, drone_id: uuid.UUID, *, for_update: bool = False) -> Drone:
    stmt = select(Drone).where(Drone.id == drone_id)
    if for_update:
        stmt = stmt.with_for_update()
    drone = db.execute(stmt).scalar_one_or_none()
    if drone is None:
        raise NotFound("Дрон не найден")
    return drone


def visible_drone(db: Session, p: Principal, drone_id: uuid.UUID) -> Drone:
    drone = get_drone(db, drone_id)
    if p.has("drone.read_all") or (p.has("drone.read_own") and drone.owner_id == p.user_id):
        return drone
    raise NotFound("Дрон не найден")


def register(db: Session, p: Principal, data: Dict[str, Any]) -> Drone:
    p.require("drone.register")
    fleet_id = data.get("fleet_id")
    if p.has("fleet.manage"):
        # Техник заводит дрон в свой парк.
        from . import fleets
        if not fleet_id:
            raise ValidationFailed("Выберите парк, в который заводится дрон", code="fleet_required")
        fleets.own_fleet(db, p, fleet_id)
    elif fleet_id:
        raise Forbidden("Заводить дроны в парк может только его техник")
    for field in ("serial_number", "registration_number"):
        if db.execute(select(Drone.id).where(getattr(Drone, field) == data[field])).first():
            raise Conflict("Дрон с таким серийным или учётным номером уже зарегистрирован", code="drone_exists")
    drone = Drone(owner_id=p.user_id, serial_number=data["serial_number"],
                  registration_number=data["registration_number"], manufacturer=data["manufacturer"],
                  model=data["model"], adapter_kind=data.get("adapter_kind", "simulator"),
                  max_payload_g=data["max_payload_g"], max_range_m=data["max_range_m"],
                  status="pending_approval", health_status="requires_check", link_status="offline",
                  fleet_id=fleet_id)
    db.add(drone)
    db.flush()
    audit.record(db, p.actor, "drone.registered", entity_type="drone", entity_id=drone.id,
                 details={k: data[k] for k in ("serial_number", "registration_number", "model")})
    notifications.create(db, notifications.users_with_role(db, "admin"), "drone.pending",
                         f"Новый дрон {drone.registration_number} ожидает подтверждения регистрации")
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[p.user_id], roles=["admin", "technician"])
    return drone


def decide_registration(db: Session, p: Principal, drone_id: uuid.UUID, action: str, reason: str) -> Drone:
    """Администратор: approve | block | unblock | retire."""
    p.require("drone.approve")
    drone = get_drone(db, drone_id, for_update=True)
    before = drone.status
    if action == "approve":
        if drone.status != "pending_approval":
            raise Conflict("Регистрация уже рассмотрена")
        drone.status = "active"
    elif action == "block":
        if not reason:
            raise ValidationFailed("Укажите причину блокировки")
        drone.status = "blocked"
        drone.blocked_reason = reason
    elif action == "unblock":
        if drone.status != "blocked":
            raise Conflict("Дрон не заблокирован")
        drone.status = "active"
        drone.blocked_reason = None
    elif action == "retire":
        drone.status = "retired"
    else:
        raise ValidationFailed("Неизвестное действие")
    drone.updated_at = clock.now()
    audit.record(db, p.actor, f"drone.{action}", entity_type="drone", entity_id=drone.id,
                 details={"before": before, "after": drone.status, "reason": reason})
    notifications.create(db, [drone.owner_id], "drone.status",
                         f"Дрон {drone.registration_number}: регистрация — {drone.status}", reason)
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[drone.owner_id],
                  roles=["admin", "technician"])
    return drone


def issue_key(db: Session, p: Principal, drone_id: uuid.UUID) -> Tuple[Drone, str]:
    """Выпуск (ротация) ключа сервисного модуля. Ключ показывается один раз."""
    p.require("drone.issue_key")
    drone = get_drone(db, drone_id, for_update=True)
    from . import fleets
    if drone.owner_id != p.user_id and fleets.fleet_technician(db, drone) != p.user_id:
        raise NotFound("Дрон не найден")
    if drone.status != "active":
        raise Conflict("Ключ можно выпустить только для дрона с подтверждённой регистрацией")
    key = new_drone_key()
    drone.api_key_hash = hash_token(key)
    drone.api_key_prefix = key[:10]
    drone.api_key_issued_at = clock.now()
    audit.record(db, p.actor, "drone.key_issued", entity_type="drone", entity_id=drone.id,
                 details={"prefix": drone.api_key_prefix})
    return drone, key


def authenticate_drone(db: Session, api_key: str) -> Drone:
    if not api_key:
        raise Forbidden("Требуется ключ устройства", code="drone_auth_required")
    drone = db.execute(select(Drone).where(Drone.api_key_hash == hash_token(api_key))).scalar_one_or_none()
    if drone is None:
        raise Forbidden("Недействительный ключ устройства", code="drone_auth_failed")
    if drone.status != "active":
        raise Forbidden("Дрон заблокирован или не допущен", code="drone_not_active")
    return drone


def record_health(db: Session, drone: Drone, *, source: str, result: str, battery_pct: Optional[int],
                  error_codes: List[str], details: Dict[str, Any], actor: audit.Actor,
                  created_by: Optional[uuid.UUID] = None, set_status: Optional[str] = None) -> DroneHealthCheck:
    if result not in ("pass", "warn", "fail"):
        raise ValidationFailed("Недопустимый результат проверки")
    check = DroneHealthCheck(drone_id=drone.id, source=source, result=result, battery_pct=battery_pct,
                             error_codes=error_codes, details=details, created_by=created_by,
                             created_at=clock.now())
    db.add(check)
    before = drone.health_status
    if source == "technician" and set_status:
        if set_status not in HEALTH_STATUSES:
            raise ValidationFailed("Недопустимый технический статус")
        if set_status == "serviceable" and result == "fail":
            raise ValidationFailed("Нельзя признать исправным дрон с непройденной проверкой")
        drone.health_status = set_status
    elif source != "technician" and result == "fail" and drone.health_status == "serviceable":
        drone.health_status = "requires_check"     # авто-понижение
    if battery_pct is not None:
        drone.battery_pct = battery_pct
    drone.updated_at = clock.now()
    db.flush()
    if before != drone.health_status:
        audit.record(db, actor, "drone.health_status_changed", entity_type="drone", entity_id=drone.id,
                     details={"before": before, "after": drone.health_status, "source": source,
                              "error_codes": error_codes})
        if drone.health_status != "serviceable":
            notifications.create(db, notifications.users_with_role(db, "technician"), "drone.health",
                                 f"Дрон {drone.registration_number}: требуется проверка",
                                 ", ".join(error_codes), severity="warning")
    events.notify(db, "drone.updated", {"drone_id": drone.id}, users=[drone.owner_id],
                  roles=["admin", "technician"])
    return check


def technician_inspection(db: Session, p: Principal, drone_id: uuid.UUID, data: Dict[str, Any]) -> DroneHealthCheck:
    p.require("drone.health.write")
    drone = get_drone(db, drone_id, for_update=True)
    check = record_health(db, drone, source="technician", result=data["result"],
                          battery_pct=data.get("battery_pct"), error_codes=data.get("error_codes", []),
                          details={"comment": data.get("comment", "")}, actor=p.actor,
                          created_by=p.user_id, set_status=data["set_status"])
    db.add(MaintenanceRecord(drone_id=drone.id, technician_id=p.user_id, kind="inspection",
                             description=data.get("comment") or "Технический осмотр",
                             health_status_set=data["set_status"], created_at=clock.now()))
    audit.record(db, p.actor, "drone.inspection", entity_type="drone", entity_id=drone.id,
                 details={"result": data["result"], "set_status": data["set_status"]})
    return check


def add_maintenance(db: Session, p: Principal, drone_id: uuid.UUID, data: Dict[str, Any]) -> MaintenanceRecord:
    p.require("maintenance.write")
    drone = get_drone(db, drone_id, for_update=True)
    rec = MaintenanceRecord(drone_id=drone.id, technician_id=p.user_id, kind=data["kind"],
                            description=data["description"], components=data.get("components", []),
                            health_status_set=data.get("set_status"), created_at=clock.now())
    db.add(rec)
    if data.get("set_status"):
        # Любые работы, кроме осмотра с результатом «pass», требуют повторной проверки перед допуском.
        if data["set_status"] == "serviceable":
            raise ValidationFailed("Статус «Исправен» устанавливается по результату технического осмотра")
        before = drone.health_status
        drone.health_status = data["set_status"]
        audit.record(db, p.actor, "drone.health_status_changed", entity_type="drone", entity_id=drone.id,
                     details={"before": before, "after": drone.health_status, "source": "maintenance"})
    db.flush()
    audit.record(db, p.actor, "drone.maintenance", entity_type="drone", entity_id=drone.id,
                 details={"kind": data["kind"], "components": data.get("components", [])})
    events.notify(db, "drone.updated", {"drone_id": drone.id}, roles=["admin", "technician"],
                  users=[drone.owner_id])
    return rec


def compute_link_status(settings: Settings, drone: Drone, now: Optional[dt.datetime] = None) -> str:
    now = now or clock.now()
    if drone.last_seen_at is None:
        return "offline"
    age = (now - drone.last_seen_at).total_seconds()
    if age <= settings.link_degraded_s:
        return "online"
    if age <= settings.drone_online_timeout_s:
        return "degraded"
    return "lost"


def readiness(db: Session, settings: Settings, drone: Drone, *, cargo_weight_g: Optional[int] = None,
              distance_m: Optional[int] = None, require_online: bool = True) -> Tuple[bool, List[str]]:
    """Проверка допуска дрона к заданию. Возвращает (готов, список причин отказа)."""
    reasons: List[str] = []
    if drone.status != "active":
        reasons.append("Регистрация дрона не подтверждена или дрон заблокирован")
    if drone.health_status != "serviceable":
        reasons.append("Дрон не допущен техником (статус: {})".format(drone.health_status))
    last = db.execute(select(DroneHealthCheck).where(DroneHealthCheck.drone_id == drone.id)
                      .order_by(DroneHealthCheck.created_at.desc()).limit(1)).scalar_one_or_none()
    if last is None:
        reasons.append("Нет результатов диагностики")
    else:
        age_h = (clock.now() - last.created_at).total_seconds() / 3600
        if age_h > settings.health_check_max_age_h:
            reasons.append("Диагностика устарела (более {} ч)".format(settings.health_check_max_age_h))
        if last.result == "fail":
            reasons.append("Последняя диагностика не пройдена: {}".format(", ".join(last.error_codes) or "ошибка"))
    if cargo_weight_g is not None and cargo_weight_g > drone.max_payload_g:
        reasons.append("Масса груза превышает грузоподъёмность дрона")
    if distance_m is not None and distance_m > drone.max_range_m:
        reasons.append("Дальность маршрута превышает допустимую для дрона")
    if require_online:
        status = compute_link_status(settings, drone)
        if status != "online":
            reasons.append("Нет связи с сервисным модулем дрона (статус связи: {})".format(status))
        if drone.battery_pct is None or drone.battery_pct < settings.min_battery_pct:
            reasons.append("Недостаточный заряд батареи (минимум {}%)".format(settings.min_battery_pct))
    return (not reasons), reasons
