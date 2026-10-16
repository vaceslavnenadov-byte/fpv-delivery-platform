"""REST API рекрутера: пользователи, роли, проверка профилей.

Заявки на допуск операторов рассматриваются через /api/v1/admin/operators (разрешение operator.approve
есть и у рекрутера, и у администратора)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import runtime
from ..models import Role
from ..services import recruitment as svc
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db, require

router = APIRouter(prefix="/api/v1/recruitment", tags=["recruitment"])


@router.get("/roles")
def roles(p: Principal = Depends(require("user.recruit")), db: Session = Depends(get_db)):
    allowed = svc.RECRUITABLE_ROLES + (svc.PRIVILEGED_ROLES if p.has("user.manage") else ())
    rows = db.execute(select(Role).where(Role.code.in_(allowed)).order_by(Role.id)).scalars()
    return {"items": [{"code": r.code, "name": r.name, "description": r.description} for r in rows]}


@router.get("/users")
def users(q: Optional[str] = None,
          profile_status: Optional[str] = Query(None, pattern="^(pending|verified|rejected)$"),
          p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": svc.list_users(db, p, q, profile_status)}


@router.post("/users", status_code=201)
def register(body: schemas.RecruitUserIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = svc.register_user(db, p, runtime.directory(), body.username, body.roles)
    db.commit()
    return {"id": str(user.id), "username": user.domain_username, "display_name": user.display_name}


@router.post("/users/{user_id}/roles")
def user_role(user_id: uuid.UUID, body: schemas.RoleGrantIn, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    svc.set_role(db, p, user_id, body.role, body.grant)
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/profile")
def profile_review(user_id: uuid.UUID, body: schemas.ProfileReviewIn, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    user = svc.review_profile(db, p, user_id, body.approve, body.notes)
    db.commit()
    return {"id": str(user.id), "profile_status": user.profile_status}
