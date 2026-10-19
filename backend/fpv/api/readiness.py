"""REST API готовности к работе («Готов к работе» / «Не готов к работе»)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..services import readiness
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(tags=["readiness"])


@router.get("/api/v1/me/readiness")
def my_readiness(role: Literal["operator", "coordinator", "technician"] = Query(...),
                 p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return readiness.mine(db, p, role)


@router.put("/api/v1/me/readiness")
def set_readiness(body: schemas.ReadinessIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    out = readiness.set_ready(db, p, body.role, body.ready)
    db.commit()
    return out
