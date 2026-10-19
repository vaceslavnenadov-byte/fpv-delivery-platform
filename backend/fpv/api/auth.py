"""Authentication API, профиль, уведомления."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import Conflict
from ..models import Notification, Operator, User
from ..services import audit, notifications, recruitment
from ..services import identity as svc
from ..services.identity import Principal
from . import schemas, serialize
from .deps import CSRF_COOKIE, SESSION_COOKIE, client_ip, current_principal, get_db, limiters

router = APIRouter(prefix="/api/v1", tags=["auth"])


def principal_dict(p: Principal) -> dict:
    return {"id": str(p.user_id), "username": p.username, "display_name": p.display_name,
            "roles": sorted(p.roles), "permissions": sorted(p.permissions), "groups": p.groups,
            "profile_completed": p.profile_completed, "operator_status": p.operator_status,
            "account_status": p.account_status, "profile_verified": p.profile_verified}


@router.post("/auth/login")
def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if limiters.login:
        limiters.login.hit(f"ip:{ip}")
        limiters.login.hit(f"user:{body.username.lower()}")
    settings = runtime.settings()
    result = svc.login(db, settings, runtime.directory(), body.username, body.password, ip=ip,
                       user_agent=request.headers.get("user-agent"), device_code=body.device_code)
    db.commit()
    max_age = settings.session_ttl_s
    response.set_cookie(SESSION_COOKIE, result.session_token, max_age=max_age, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, result.csrf_token, max_age=max_age, httponly=False,
                        secure=settings.cookie_secure, samesite="strict", path="/")
    return {"user": principal_dict(result.principal), "csrf_token": result.csrf_token,
            "expires_at": result.expires_at.isoformat()}


@router.post("/auth/logout")
def logout(response: Response, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    svc.logout(db, p)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = db.get(User, p.user_id)
    data = principal_dict(p)
    data.update({"email": user.email, "phone": user.phone, "last_login_at": serialize.ts(user.last_login_at),
                 "profile_status": user.profile_status, "profile_review_notes": user.profile_review_notes})
    from ..services import people
    data["profile"] = people.report_identity(db, user.id)
    return data


@router.put("/me/profile")
def update_profile(body: schemas.ProfileIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Регистрация в приложении: заполнение профиля после первого доменного входа."""
    p.require("profile.self")
    user = db.get(User, p.user_id)
    before = {"display_name": user.display_name, "phone": user.phone, "email": user.email}
    user.display_name = body.display_name
    user.phone = body.phone
    if body.email:
        user.email = body.email
    # никнейм и местоположение не требуют повторной проверки профиля
    user.nickname = (body.nickname or "").strip() or None
    user.location = (body.location or "").strip() or None
    first = not user.profile_completed
    changed = first or before != {"display_name": user.display_name, "phone": user.phone, "email": user.email}
    user.profile_completed = True
    user.updated_at = clock.now()
    audit.record(db, p.actor, "user.profile_updated", entity_type="user", entity_id=user.id,
                 details={"before": before, "after": body.model_dump()})
    if changed:
        # изменённый профиль заново проверяет рекрутер (или администратор, если рекрутеров нет)
        user.profile_status = "pending"
        user.profile_reviewed_by = user.profile_reviewed_at = None
        notifications.create(db, [u for u in recruitment.recruiters(db) if u != user.id], "user.profile_review",
                             f"Профиль на проверку: {user.display_name}", user.domain_username)
    db.commit()
    return {"ok": True}


@router.post("/me/operator-application", status_code=201)
def operator_application(body: schemas.OperatorApplyIn, p: Principal = Depends(current_principal),
                         db: Session = Depends(get_db)):
    """Заявка на допуск оператора. Права оператора действуют после подтверждения администратором."""
    p.require("operator.apply")
    op = db.get(Operator, p.user_id)
    if op is not None and op.status in ("pending", "approved"):
        raise Conflict("Заявка уже подана или одобрена")
    if op is None:
        op = Operator(user_id=p.user_id, license_number=body.license_number, notes=body.notes,
                      created_at=clock.now())
        db.add(op)
    else:
        op.status, op.license_number, op.notes = "pending", body.license_number, body.notes
    db.flush()
    audit.record(db, p.actor, "operator.applied", entity_type="user", entity_id=p.user_id,
                 details={"license_number": body.license_number})
    notifications.create(db, recruitment.recruiters(db), "operator.application",
                         f"Заявка оператора: {p.display_name}")
    db.commit()
    return {"status": op.status}


@router.get("/me/notifications")
def my_notifications(unread: bool = False, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.require("notification.read")
    stmt = select(Notification).where(Notification.user_id == p.user_id)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(100)).scalars()
    unread_count = db.query(Notification).filter(Notification.user_id == p.user_id,
                                                 Notification.read_at.is_(None)).count()
    return {"unread": unread_count,
            "items": [{"id": n.id, "kind": n.kind, "severity": n.severity, "title": n.title, "body": n.body,
                       "order_id": str(n.order_id) if n.order_id else None, "read": n.read_at is not None,
                       "created_at": serialize.ts(n.created_at)} for n in rows]}


@router.post("/me/notifications/read")
def mark_read(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
               .values(read_at=clock.now()))
    db.commit()
    return {"ok": True}
