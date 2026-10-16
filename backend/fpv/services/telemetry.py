"""Telemetry Ingestion Service и контроль связи с дронами.

Отдельный контур: данные принимаются только от сервисного модуля дрона,
аутентифицированного собственным ключом (не пользовательской сессией).
Контур односторонний: сервер не передаёт дрону команд управления полётом,
только справочную информацию о задании (маршрут, статус заказа).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..models import Drone, Order, TelemetryRecord
from . import audit, drones, events, notifications, orders

TELEMETRY_FIELDS = ("lat", "lon", "alt_m", "ground_speed_mps", "heading_deg", "battery_pct", "link_quality",
                    "gnss_fix", "flight_phase")


def drone_actor(drone: Drone) -> audit.Actor:
    return audit.Actor(None, f"drone:{drone.serial_number}")


def current_order(db: Session, drone: Drone, statuses=("in_progress",), for_update: bool = False) -> Optional[Order]:
    stmt = select(Order).where(Order.drone_id == drone.id, Order.status.in_(statuses))
    if for_update:
        stmt = stmt.with_for_update(of=Order)
    return db.execute(stmt.order_by(Order.updated_at.desc()).limit(1)).unique().scalar_one_or_none()


def mark_seen(db: Session, settings: Settings, drone: Drone, battery_pct: Optional[int] = None) -> None:
    before = drone.link_status
    drone.last_seen_at = clock.now()
    drone.link_status = "online"
    if battery_pct is not None:
        drone.battery_pct = battery_pct
    if before in ("lost", "degraded"):
        order = current_order(db, drone)
        audit.record(db, drone_actor(drone), "drone.link_restored", entity_type="drone", entity_id=drone.id,
                     details={"previous": before})
        if order is not None:
            notifications.create(db, [order.operator_id, order.coordinator_id], "drone.link",
                                 f"Связь с дроном заказа №{order.number} восстановлена", order_id=order.id,
                                 dedup_key=f"link-restored:{order.id}:{int(drone.last_seen_at.timestamp())}")
        events.notify(db, "drone.link", {"drone_id": drone.id, "link_status": "online"},
                      roles=["admin", "technician"], users=[drone.owner_id])


def ingest(db: Session, settings: Settings, drone: Drone, records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Пакетный приём телеметрии. Дубликаты (повторная отправка после переподключения)
    отбрасываются по уникальному ключу (drone_id, seq)."""
    order = current_order(db, drone)
    now = clock.now()
    rows = []
    for r in records:
        recorded = r["recorded_at"]
        if recorded > now + dt.timedelta(minutes=5):
            recorded = now          # защита от неверных часов устройства
        row = {"drone_id": drone.id, "order_id": order.id if order else None, "seq": r["seq"],
               "recorded_at": recorded, "received_at": now,
               "extra": r.get("extra") or {}}
        for f in TELEMETRY_FIELDS:
            row[f] = r.get(f)
        rows.append(row)
    inserted = 0
    if rows:
        res = db.execute(pg_insert(TelemetryRecord).values(rows)
                         .on_conflict_do_nothing(index_elements=["drone_id", "seq"])
                         .returning(TelemetryRecord.id))
        inserted = len(res.all())
    last_battery = next((r.get("battery_pct") for r in reversed(records) if r.get("battery_pct") is not None), None)
    mark_seen(db, settings, drone, last_battery)
    if order is not None and records:
        last = records[-1]
        events.notify(db, "telemetry", {"order_id": order.id, "drone_id": drone.id,
                                        "lat": last.get("lat"), "lon": last.get("lon"), "alt_m": last.get("alt_m"),
                                        "battery_pct": last.get("battery_pct"),
                                        "link_quality": last.get("link_quality"),
                                        "flight_phase": last.get("flight_phase")},
                      users=[order.operator_id, order.coordinator_id], roles=["admin"])
    return {"received": len(records), "stored": inserted, "duplicates": len(records) - inserted}


def handle_device_event(db: Session, settings: Settings, drone: Drone, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """События сервисного модуля: delivery_completed, error, self_check."""
    mark_seen(db, settings, drone)
    actor = drone_actor(drone)
    if kind == "delivery_completed":
        order = current_order(db, drone, statuses=("in_progress", "awaiting_delivery_confirmation"), for_update=True)
        if order is None:
            return {"handled": False, "reason": "Нет выполняемого заказа"}
        orders.complete(db, settings, actor, order, actor_kind="drone")
        return {"handled": True, "order_id": str(order.id), "status": order.status}
    if kind == "error":
        severity = data.get("severity", "warning")
        code = str(data.get("code", "unknown"))[:64]
        drones.record_health(db, drone, source="device", result="fail" if severity == "critical" else "warn",
                             battery_pct=data.get("battery_pct"), error_codes=[code],
                             details={"message": str(data.get("message", ""))[:500]}, actor=actor)
        order = current_order(db, drone, for_update=True)
        audit.record(db, actor, "drone.error", entity_type="drone", entity_id=drone.id,
                     details={"code": code, "severity": severity, "order_id": order.id if order else None})
        if order is not None:
            notifications.create(db, [order.operator_id, order.coordinator_id], "drone.error",
                                 f"Заказ №{order.number}: ошибка дрона {code}", str(data.get("message", ""))[:300],
                                 order_id=order.id, severity="critical" if severity == "critical" else "warning")
            if severity == "critical":
                # Безопасная процедура платформы: задание прерывается, дрон снимается с эксплуатации до проверки.
                # Аварийные действия в воздухе (возврат/посадка) выполняет бортовая система дрона.
                orders.abort(db, settings, actor, order, f"Критическая ошибка дрона: {code}", actor_kind="drone")
        return {"handled": True}
    if kind == "self_check":
        result = data.get("result", "pass")
        drones.record_health(db, drone, source="device", result=result, battery_pct=data.get("battery_pct"),
                             error_codes=[str(c)[:64] for c in data.get("error_codes", [])][:20],
                             details={k: data[k] for k in ("firmware_version", "sensors") if k in data},
                             actor=actor)
        if data.get("firmware_version"):
            drone.firmware_version = str(data["firmware_version"])[:64]
        return {"handled": True}
    return {"handled": False, "reason": "Неизвестный тип события"}


def mission_info(db: Session, drone: Drone) -> Dict[str, Any]:
    """Справочная информация о задании для сервисного модуля (не команды)."""
    order = current_order(db, drone, statuses=("ready", "in_progress"))
    if order is None:
        return {"active": False, "video_source": drone.video_source_selected}
    r = order.route
    return {"active": True, "order_id": str(order.id), "number": order.number, "status": order.status,
            "route": {"origin": {"lat": r.origin_lat, "lon": r.origin_lon},
                      "destination": {"lat": r.dest_lat, "lon": r.dest_lon},
                      "checkpoints": r.checkpoints, "max_altitude_m": r.max_altitude_m},
            "video_buffer_seconds": None,
            # выбранный оператором источник видео (настройка видео, не команда полёта)
            "video_source": drone.video_source_selected}


def monitor_links(db: Session, settings: Settings) -> Dict[str, int]:
    """Плановая проверка связи. Безопасная процедура при потере связи:
    предупреждение → (по истечении link_loss_abort_s) прерывание задания и
    перевод дрона в «Требует проверки»."""
    stats = {"degraded": 0, "lost": 0, "aborted": 0}
    now = clock.now()
    for drone in db.execute(select(Drone).where(Drone.link_status.in_(("online", "degraded", "lost")))
                            .with_for_update(skip_locked=True)).scalars():
        status = drones.compute_link_status(settings, drone, now)
        if status == drone.link_status:
            pass
        else:
            drone.link_status = status
            events.notify(db, "drone.link", {"drone_id": drone.id, "link_status": status},
                          roles=["admin", "technician"], users=[drone.owner_id])
        order = current_order(db, drone, for_update=True)
        if status in ("degraded", "lost") and order is not None:
            stats[status] += 1
            age = int((now - drone.last_seen_at).total_seconds()) if drone.last_seen_at else None
            notifications.create(db, [order.operator_id, order.coordinator_id], "drone.link",
                                 f"Заказ №{order.number}: {'нестабильная связь' if status == 'degraded' else 'потеря связи'} с дроном",
                                 f"Нет данных {age} с", order_id=order.id,
                                 severity="warning" if status == "degraded" else "critical",
                                 dedup_key=f"link:{status}:{order.id}:{drone.last_seen_at.isoformat() if drone.last_seen_at else ''}")
            if status == "lost" and age is not None and age >= settings.link_loss_abort_s:
                orders.abort(db, settings, audit.Actor.system("link-monitor"), order,
                             f"Потеря связи с дроном более {settings.link_loss_abort_s} с", actor_kind="system")
                stats["aborted"] += 1
        if status == "lost" and order is None and drone.last_seen_at and \
                (now - drone.last_seen_at).total_seconds() > 3600:
            drone.link_status = "offline"
    return stats
