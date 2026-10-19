"""REST API пользователей (рекрутер и администратор) и внутренних групп."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..services import org_units, people
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(tags=["people"])


# --- внутренние группы ---

@router.get("/api/v1/org-units")
def list_units(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": org_units.list_units(db, p)}


@router.post("/api/v1/org-units", status_code=201)
def create_unit(body: schemas.OrgUnitIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    u = org_units.create(db, p, body.name, body.parent_id)
    db.commit()
    return {"id": str(u.id), "name": u.name}


@router.patch("/api/v1/org-units/{unit_id}")
def update_unit(unit_id: uuid.UUID, body: schemas.OrgUnitPatch, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    data = {}
    if body.name is not None:
        data["name"] = body.name
    if body.move:
        data["parent_id"] = body.parent_id
    u = org_units.update(db, p, unit_id, data)
    db.commit()
    return {"id": str(u.id), "name": u.name}


@router.delete("/api/v1/org-units/{unit_id}", status_code=204)
def delete_unit(unit_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    org_units.delete(db, p, unit_id)
    db.commit()


# --- пользователи ---

@router.get("/api/v1/people")
def list_people(q: Optional[str] = None, status: Optional[str] = Query(None, pattern="^(active|suspended|banned|disabled)$"),
                org_unit: Optional[uuid.UUID] = None, role: Optional[str] = None,
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": people.list_people(db, p, q=q, status=status, org_unit=org_unit, role=role)}


@router.post("/api/v1/people", status_code=201)
def create_person(body: schemas.LocalUserIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    u = people.create_local(db, p, body.model_dump())
    db.commit()
    return people.card(db, p, u.id)


@router.get("/api/v1/people/{user_id}")
def person(user_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return people.card(db, p, user_id)


@router.get("/api/v1/people/{user_id}/history")
def person_history(user_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": people.history(db, p, user_id)}


@router.patch("/api/v1/people/{user_id}/profile")
def person_profile(user_id: uuid.UUID, body: schemas.PersonProfileIn, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    people.update_profile(db, p, user_id, body.model_dump(exclude_unset=True))
    db.commit()
    return people.card(db, p, user_id)


@router.post("/api/v1/people/{user_id}/credentials")
def person_credentials(user_id: uuid.UUID, body: schemas.CredentialsIn, p: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    people.set_credentials(db, p, user_id, username=body.username, password=body.password)
    db.commit()
    return people.card(db, p, user_id)


@router.post("/api/v1/people/{user_id}/status")
def person_status(user_id: uuid.UUID, body: schemas.AccountStatusIn, p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db)):
    people.set_status(db, p, user_id, body.status, body.reason)
    db.commit()
    return people.card(db, p, user_id)
