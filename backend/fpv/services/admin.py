"""Administration Service: пользователи, роли, доменные группы, операторы, устройства,
статистика и мониторинг."""
from __future__ import annotations

import os
import shutil
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import (Device, DeviceAccessPolicy, DomainGroup, Drone, Operator, Order, Role, ScoreTransaction,
                      SessionRecord, User, UserRole, VideoEvidence)
from . import audit, events, notifications
from .identity import Principal, revoke_user_sessions


def _role(db: Session, code: str) -> Role:
    role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
    if role is None:
        raise NotFound("Роль не найдена")
    return role


def set_user_status(db: Session, p: Principal, user_id: uuid.UUID, status: str, reason: str) -> User:
    """Блокировка/разблокировка доступа к приложению. 'deleted' — обезличивание без удаления истории."""
    p.require("user.manage")
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Пользователь не найден")
    if user.id == p.user_id and status != "active":
        raise ValidationFailed("Нельзя заблокировать собственную учётную запись")
    if user.status == "deleted":
        raise Conflict("Учётная запись удалена")
    before = user.status
    user.status = status
    user.updated_at = clock.now()
    if status != "active":
        revoke_user_sessions(db, user.id, f"user_{status}")
    if status == "deleted":
        # Персональные данные обезличиваются; заказы, баллы и аудит сохраняются.
        user.display_name = "Удалённый пользователь"
        user.email = None
        user.phone = None
        user.deleted_at = clock.now()
    audit.record(db, p.actor, "user.status_changed", entity_type="user", entity_id=user.id,
                 details={"before": before, "after": status, "reason": reason})
    return user


def set_manual_role(db: Session, p: Principal, user_id: uuid.UUID, role_code: str, grant: bool) -> None:
    p.require("user.manage")
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Пользователь не найден")
    role = _role(db, role_code)
    if grant:
        if not db.get(UserRole, (user.id, role.id, "manual")):
            db.add(UserRole(user_id=user.id, role_id=role.id, source="manual", granted_by=p.user_id,
                            granted_at=clock.now()))
    else:
        db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id,
                                          UserRole.source == "manual"))
    db.flush()
    audit.record(db, p.actor, "user.role_granted" if grant else "user.role_revoked", entity_type="user",
                 entity_id=user.id, details={"role": role_code, "source": "manual"})


def upsert_domain_group(db: Session, p: Principal, data: Dict[str, Any]) -> DomainGroup:
    p.require("user.manage")
    group = db.execute(select(DomainGroup).where(DomainGroup.name == data["name"])).scalar_one_or_none()
    role_id = _role(db, data["role"]).id if data.get("role") else None
    before = None
    if group is None:
        group = DomainGroup(dn=data["dn"], name=data["name"], role_id=role_id, description=data.get("description", ""))
        db.add(group)
    else:
        before = {"dn": group.dn, "role_id": group.role_id}
        group.dn, group.role_id, group.description = data["dn"], role_id, data.get("description", "")
    db.flush()
    audit.record(db, p.actor, "domain_group.mapped", entity_type="domain_group", entity_id=group.id,
                 details={"before": before, "after": {"dn": group.dn, "role": data.get("role")}})
    return group


def decide_operator(db: Session, p: Principal, user_id: uuid.UUID, approve: bool, notes: str) -> Operator:
    p.require("operator.approve")
    op = db.get(Operator, user_id)
    if op is None:
        raise NotFound("Заявка оператора не найдена")
    before = op.status
    op.status = "approved" if approve else "rejected"
    op.decided_by = p.user_id
    op.decided_at = clock.now()
    if notes:
        op.notes = notes
    audit.record(db, p.actor, "operator.decision", entity_type="user", entity_id=user_id,
                 details={"before": before, "after": op.status, "notes": notes})
    notifications.create(db, [user_id], "operator.decision",
                         "Допуск оператора подтверждён" if approve else "Заявка оператора отклонена", notes)
    db.flush()
    from . import operator_dispatch
    operator_dispatch.sweep(db)          # допущенному — ждущие заказы, отклонённому — больше не предлагать
    return op


def suspend_operator(db: Session, p: Principal, user_id: uuid.UUID, notes: str) -> Operator:
    p.require("operator.approve")
    op = db.get(Operator, user_id)
    if op is None:
        raise NotFound("Оператор не найден")
    before = op.status
    op.status = "suspended"
    op.decided_by, op.decided_at = p.user_id, clock.now()
    audit.record(db, p.actor, "operator.suspended", entity_type="user", entity_id=user_id,
                 details={"before": before, "notes": notes})
    db.flush()
    from . import operator_dispatch
    operator_dispatch.sweep(db)
    return op


def create_device(db: Session, p: Principal, data: Dict[str, Any]) -> Device:
    p.require("device.manage")
    if db.execute(select(Device.id).where(Device.code == data["code"])).first():
        raise Conflict("Устройство с таким кодом уже существует")
    device = Device(**data)
    db.add(device)
    db.flush()
    audit.record(db, p.actor, "device.registered", entity_type="device", entity_id=device.id, details=data)
    return device


def add_device_policy(db: Session, p: Principal, device_id: uuid.UUID, data: Dict[str, Any]) -> DeviceAccessPolicy:
    p.require("device.manage")
    device = db.get(Device, device_id)
    if device is None:
        raise NotFound("Устройство не найдено")
    role_id = _role(db, data["role"]).id if data.get("role") else None
    group_id = None
    if data.get("domain_group"):
        group = db.execute(select(DomainGroup).where(DomainGroup.name == data["domain_group"])).scalar_one_or_none()
        if group is None:
            raise NotFound("Доменная группа не найдена")
        group_id = group.id
    policy = DeviceAccessPolicy(device_id=device.id, role_id=role_id, domain_group_id=group_id,
                                access_level=data.get("access_level", "use"))
    db.add(policy)
    db.flush()
    audit.record(db, p.actor, "device.policy_added", entity_type="device", entity_id=device.id, details=data)
    return policy


def remove_device_policy(db: Session, p: Principal, policy_id: int) -> None:
    p.require("device.manage")
    policy = db.get(DeviceAccessPolicy, policy_id)
    if policy is None:
        raise NotFound("Политика не найдена")
    db.delete(policy)
    audit.record(db, p.actor, "device.policy_removed", entity_type="device", entity_id=policy.device_id,
                 details={"policy_id": policy_id})


def statistics(db: Session, p: Principal) -> Dict[str, Any]:
    p.require("report.read")
    by_status = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    drones_by_health = dict(db.execute(select(Drone.health_status, func.count()).group_by(Drone.health_status)).all())
    video_by_status = dict(db.execute(select(VideoEvidence.status, func.count()).group_by(VideoEvidence.status)).all())
    top = db.execute(select(User.display_name, Operator.total_points, Operator.completed_orders)
                     .join(Operator, Operator.user_id == User.id)
                     .where(Operator.status == "approved")
                     .order_by(Operator.total_points.desc()).limit(10)).all()
    avg = db.execute(select(func.avg(ScoreTransaction.points)).where(ScoreTransaction.kind == "final")).scalar()
    return {
        "orders_by_status": by_status,
        "orders_total": sum(by_status.values()),
        "drones_by_health": drones_by_health,
        "video_by_status": video_by_status,
        "average_final_score": round(float(avg), 1) if avg is not None else None,
        "top_operators": [{"name": r[0], "points": r[1], "completed": r[2]} for r in top],
    }


def monitoring(db: Session, p: Principal) -> Dict[str, Any]:
    p.require("system.monitor")
    settings = runtime.settings()
    services: List[Dict[str, Any]] = []
    try:
        version = db.execute(text("SHOW server_version")).scalar()
        size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        services.append({"name": "PostgreSQL", "ok": True, "details": {"version": version, "size_bytes": size}})
    except Exception as exc:  # pragma: no cover
        services.append({"name": "PostgreSQL", "ok": False, "details": {"error": exc.__class__.__name__}})
    try:
        runtime.directory().ping()
        services.append({"name": "Служба каталогов", "ok": True, "details": {"provider": runtime.directory().name}})
    except Exception as exc:
        services.append({"name": "Служба каталогов", "ok": False, "details": {"error": str(exc)[:200]}})
    try:
        services.append({"name": "Хранилище видео", "ok": True, "details": runtime.store().ping()})
    except Exception as exc:  # pragma: no cover
        services.append({"name": "Хранилище видео", "ok": False, "details": {"error": str(exc)[:200]}})
    try:
        os.makedirs(settings.video_spool_dir, exist_ok=True)
        usage = shutil.disk_usage(settings.video_spool_dir)
        services.append({"name": "Буфер видео (spool)", "ok": usage.free > 1024 ** 3,
                         "details": {"free_bytes": usage.free, "total_bytes": usage.total}})
    except OSError as exc:  # pragma: no cover
        services.append({"name": "Буфер видео (spool)", "ok": False, "details": {"error": str(exc)}})
    outbox = events.outbox_stats(db)
    services.append({"name": "Очередь событий (outbox)", "ok": outbox.get("dead", 0) == 0, "details": outbox})
    links = dict(db.execute(select(Drone.link_status, func.count()).group_by(Drone.link_status)).all())
    active_sessions = db.execute(select(func.count()).select_from(SessionRecord).where(
        SessionRecord.revoked_at.is_(None), SessionRecord.expires_at > clock.now())).scalar()
    connected = list(db.execute(select(Drone).where(Drone.link_status.in_(("online", "degraded", "lost")))
                                .order_by(Drone.last_seen_at.desc()).limit(100)).scalars())
    devices = list(db.execute(select(Device).order_by(Device.code)).scalars())
    return {
        "services": services,
        "drone_links": links,
        "active_sessions": active_sessions,
        "connected_drones": [{"id": str(d.id), "registration_number": d.registration_number,
                              "link_status": d.link_status, "battery_pct": d.battery_pct,
                              "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}
                             for d in connected],
        "devices": [{"id": str(d.id), "code": d.code, "kind": d.kind, "hostname": d.hostname,
                     "domain_joined": d.domain_joined, "status": d.status} for d in devices],
        "checked_at": clock.now().isoformat(),
    }
