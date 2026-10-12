"""REST API администрирования (Administration Service, Audit Service, Scoring rules)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import runtime
from ..errors import NotFound
from ..models import (AuditLog, Complaint, Device, DeviceAccessPolicy, DomainGroup, Operator, Permission,
                      PermissionMatrix, Role, User, UserDomainGroup, UserRole, VideoEvidence)
from ..services import admin as svc
from ..services import audit, complaints, identity, scoring, video
from ..services.identity import Principal
from . import schemas, serialize
from .deps import current_principal, get_db, require

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# --- пользователи и роли ---

@router.get("/users")
def users(q: Optional[str] = None, p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.domain_username.ilike(like), User.display_name.ilike(like)))
    rows = list(db.execute(stmt.order_by(User.domain_username).limit(500)).scalars())
    role_rows = db.execute(select(UserRole.user_id, Role.code, UserRole.source)
                           .join(Role, Role.id == UserRole.role_id)).all()
    roles = {}
    for uid, code, source in role_rows:
        roles.setdefault(uid, []).append({"role": code, "source": source})
    ops = {o.user_id: o for o in db.execute(select(Operator)).scalars()}
    return {"items": [{
        "id": str(u.id), "username": u.domain_username, "display_name": u.display_name, "email": u.email,
        "status": u.status, "directory_enabled": u.directory_enabled, "roles": roles.get(u.id, []),
        "operator_status": ops[u.id].status if u.id in ops else None,
        "last_login_at": serialize.ts(u.last_login_at), "last_directory_sync_at": serialize.ts(u.last_directory_sync_at),
    } for u in rows]}


@router.get("/users/{user_id}")
def user_detail(user_id: uuid.UUID, p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise NotFound("Пользователь не найден")
    groups = db.execute(select(DomainGroup.name, DomainGroup.dn).join(
        UserDomainGroup, UserDomainGroup.domain_group_id == DomainGroup.id).where(UserDomainGroup.user_id == u.id)).all()
    principal = identity.build_principal(db, u)
    return {"id": str(u.id), "username": u.domain_username, "upn": u.domain_upn, "object_id": u.domain_object_id,
            "display_name": u.display_name, "status": u.status, "directory_enabled": u.directory_enabled,
            "groups": [{"name": g[0], "dn": g[1]} for g in groups], "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions)}


@router.post("/users/{user_id}/status")
def user_status(user_id: uuid.UUID, body: schemas.UserStatusIn, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    u = svc.set_user_status(db, p, user_id, body.status, body.reason)
    db.commit()
    return {"id": str(u.id), "status": u.status}


@router.post("/users/{user_id}/roles")
def user_role(user_id: uuid.UUID, body: schemas.RoleGrantIn, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    svc.set_manual_role(db, p, user_id, body.role, body.grant)
    db.commit()
    return {"ok": True}


@router.get("/roles")
def roles(p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    matrix = db.execute(select(Role.code, PermissionMatrix.permission)
                        .join(PermissionMatrix, PermissionMatrix.role_id == Role.id)).all()
    by_role = {}
    for code, perm in matrix:
        by_role.setdefault(code, []).append(perm)
    return {
        "roles": [{"code": r.code, "name": r.name, "description": r.description,
                   "permissions": sorted(by_role.get(r.code, []))}
                  for r in db.execute(select(Role).order_by(Role.id)).scalars()],
        "permissions": [{"code": x.code, "description": x.description}
                        for x in db.execute(select(Permission).order_by(Permission.code)).scalars()],
    }


@router.get("/domain-groups")
def domain_groups(p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    rows = db.execute(select(DomainGroup).order_by(DomainGroup.name)).scalars()
    return {"items": [{"id": g.id, "dn": g.dn, "name": g.name, "role": g.role.code if g.role else None,
                       "description": g.description} for g in rows]}


@router.post("/domain-groups")
def upsert_group(body: schemas.DomainGroupIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    g = svc.upsert_domain_group(db, p, body.model_dump())
    db.commit()
    return {"id": g.id}


@router.post("/directory/sync")
def directory_sync(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require("user.manage")
    stats = identity.sync_all_users(db, runtime.directory())
    audit.record(db, p.actor, "directory.sync", details=stats)
    db.commit()
    return stats


# --- операторы ---

@router.get("/operators")
def operators(p: Principal = Depends(require("operator.approve")), db: Session = Depends(get_db)):
    rows = db.execute(select(Operator, User).join(User, User.id == Operator.user_id)
                      .order_by(Operator.created_at.desc())).all()
    return {"items": [{"user_id": str(o.user_id), "display_name": u.display_name, "username": u.domain_username,
                       "status": o.status, "license_number": o.license_number, "notes": o.notes,
                       "total_points": o.total_points, "completed_orders": o.completed_orders,
                       "created_at": serialize.ts(o.created_at), "decided_at": serialize.ts(o.decided_at)}
                      for o, u in rows]}


@router.post("/operators/{user_id}/decision")
def operator_decision(user_id: uuid.UUID, body: schemas.OperatorDecisionIn, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db)):
    op = svc.decide_operator(db, p, user_id, body.approve, body.notes)
    db.commit()
    return {"status": op.status}


@router.post("/operators/{user_id}/suspend")
def operator_suspend(user_id: uuid.UUID, body: schemas.OperatorDecisionIn, p: Principal = Depends(current_principal),
                     db: Session = Depends(get_db)):
    op = svc.suspend_operator(db, p, user_id, body.notes)
    db.commit()
    return {"status": op.status}


@router.get("/coordinators")
def coordinators(p: Principal = Depends(require("order.assign")), db: Session = Depends(get_db)):
    from ..services.notifications import users_with_role
    ids = users_with_role(db, "coordinator")
    return {"items": [serialize.user_brief(db, uid) for uid in ids]}


# --- устройства ---

@router.get("/devices")
def devices(p: Principal = Depends(require("device.manage")), db: Session = Depends(get_db)):
    devs = list(db.execute(select(Device).order_by(Device.code)).scalars())
    pols = list(db.execute(select(DeviceAccessPolicy)).scalars())
    role_codes = {r.id: r.code for r in db.execute(select(Role)).scalars()}
    groups = {g.id: g.name for g in db.execute(select(DomainGroup)).scalars()}
    return {"items": [{
        "id": str(d.id), "code": d.code, "kind": d.kind, "hostname": d.hostname, "description": d.description,
        "domain_joined": d.domain_joined, "status": d.status,
        "policies": [{"id": x.id, "role": role_codes.get(x.role_id), "domain_group": groups.get(x.domain_group_id),
                      "access_level": x.access_level} for x in pols if x.device_id == d.id],
    } for d in devs]}


@router.post("/devices", status_code=201)
def create_device(body: schemas.DeviceIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    d = svc.create_device(db, p, body.model_dump())
    db.commit()
    return {"id": str(d.id)}


@router.post("/devices/{device_id}/policies", status_code=201)
def add_policy(device_id: uuid.UUID, body: schemas.DevicePolicyIn, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    pol = svc.add_device_policy(db, p, device_id, body.model_dump())
    db.commit()
    return {"id": pol.id}


@router.delete("/device-policies/{policy_id}")
def delete_policy(policy_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    svc.remove_device_policy(db, p, policy_id)
    db.commit()
    return {"ok": True}


# --- аудит ---

@router.get("/audit")
def audit_log(action: Optional[str] = None, entity_id: Optional[str] = None, actor: Optional[str] = None,
              before_id: Optional[int] = None, limit: int = Query(100, ge=1, le=500),
              p: Principal = Depends(require("audit.read")), db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"{action}%"))
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor:
        stmt = stmt.where(AuditLog.actor_label.ilike(f"%{actor}%"))
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit)).scalars()
    return {"items": [{"id": r.id, "occurred_at": serialize.ts(r.occurred_at), "actor": r.actor_label,
                       "actor_ip": str(r.actor_ip) if r.actor_ip else None, "action": r.action,
                       "entity_type": r.entity_type, "entity_id": r.entity_id, "outcome": r.outcome,
                       "details": r.details, "hash": r.hash} for r in rows]}


@router.post("/audit/verify")
def audit_verify(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require("audit.read")
    result = audit.verify_chain(db)
    audit.record(db, p.actor, "audit.verified", details=result)
    db.commit()
    return result


# --- жалобы, баллы ---

@router.get("/complaints")
def complaint_list(status: Optional[str] = None, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    rows = complaints.list_complaints(db, p, status)
    return {"items": [{"id": c.id, "order_id": str(c.order_id), "kind": c.kind, "status": c.status,
                       "description": c.description, "resolution": c.resolution,
                       "points_adjustment": c.points_adjustment, "author": serialize.user_brief(db, c.author_id),
                       "created_at": serialize.ts(c.created_at), "resolved_at": serialize.ts(c.resolved_at)}
                      for c in rows]}


@router.post("/complaints/{complaint_id}/resolve")
def complaint_resolve(complaint_id: int, body: schemas.ResolveComplaintIn, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db)):
    c = complaints.resolve(db, p, complaint_id, upheld=body.upheld, resolution=body.resolution,
                           points_adjustment=body.points_adjustment)
    db.commit()
    return {"id": c.id, "status": c.status}


@router.get("/scoring-rules")
def get_rules(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = scoring.active_rules(db)
    return {"version": r.version, "rules": r.rules, "comment": r.comment, "created_at": serialize.ts(r.created_at)}


@router.put("/scoring-rules")
def put_rules(body: schemas.ScoringRulesIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = scoring.set_rules(db, p, body.rules, body.comment)
    db.commit()
    return {"version": r.version}


# --- видео ---

@router.get("/videos")
def videos(review_status: Optional[str] = None, p: Principal = Depends(require("video.read")),
           db: Session = Depends(get_db)):
    stmt = select(VideoEvidence)
    if review_status:
        stmt = stmt.where(VideoEvidence.review_status == review_status)
    rows = db.execute(stmt.order_by(VideoEvidence.created_at.desc()).limit(300)).scalars()
    return {"items": [dict(serialize.video(v, p), order_id=str(v.order_id)) for v in rows]}


@router.post("/videos/{evidence_id}/verify")
def video_verify(evidence_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require("video.read")
    ev = db.get(VideoEvidence, evidence_id)
    if ev is None:
        raise NotFound("Видеоподтверждение не найдено")
    ok = video.verify_integrity(db, ev, p.actor, p.user_id)
    db.commit()
    return {"ok": ok, "status": ev.status}


# --- отчёты и мониторинг ---

@router.get("/stats")
def stats(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return svc.statistics(db, p)


@router.get("/monitoring")
def monitoring(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return svc.monitoring(db, p)
