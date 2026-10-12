"""REST API реестра дронов и технического обслуживания."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Drone, DroneHealthCheck, MaintenanceRecord
from ..services import drones as svc
from ..services.identity import Principal
from . import schemas, serialize
from .deps import current_principal, get_db

router = APIRouter(prefix="/api/v1/drones", tags=["drones"])


@router.get("")
def list_drones(scope: str = Query("mine", pattern="^(mine|all)$"), status: Optional[str] = None,
                health_status: Optional[str] = None,
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    stmt = select(Drone)
    if scope == "all":
        p.require("drone.read_all")
    else:
        p.require("drone.read_own")
        stmt = stmt.where(Drone.owner_id == p.user_id)
    if status:
        stmt = stmt.where(Drone.status == status)
    if health_status:
        stmt = stmt.where(Drone.health_status == health_status)
    rows = db.execute(stmt.order_by(Drone.created_at.desc()).limit(1000)).scalars()
    return {"items": [serialize.drone(d) for d in rows]}


@router.post("", status_code=201)
def register(body: schemas.DroneIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = svc.register(db, p, body.model_dump())
    data = serialize.drone(drone)
    db.commit()
    return data


@router.get("/{drone_id}")
def get_drone(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = svc.visible_drone(db, p, drone_id)
    checks = db.execute(select(DroneHealthCheck).where(DroneHealthCheck.drone_id == drone.id)
                        .order_by(DroneHealthCheck.created_at.desc()).limit(50)).scalars()
    data = serialize.drone(drone)
    data["health_checks"] = [serialize.health_check(c) for c in checks]
    if p.has("maintenance.read"):
        recs = db.execute(select(MaintenanceRecord).where(MaintenanceRecord.drone_id == drone.id)
                          .order_by(MaintenanceRecord.created_at.desc()).limit(100)).scalars()
        data["maintenance"] = [serialize.maintenance(m) for m in recs]
    from .. import runtime
    ok, reasons = svc.readiness(db, runtime.settings(), drone)
    data["readiness"] = {"ready": ok, "reasons": reasons}
    return data


@router.post("/{drone_id}/key")
def issue_key(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone, key = svc.issue_key(db, p, drone_id)
    db.commit()
    # Ключ возвращается однократно; в БД хранится только хеш.
    return {"api_key": key, "prefix": drone.api_key_prefix, "issued_at": serialize.ts(drone.api_key_issued_at)}


@router.post("/{drone_id}/decision")
def decide(drone_id: uuid.UUID, body: schemas.DroneDecisionIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    drone = svc.decide_registration(db, p, drone_id, body.action, body.reason)
    data = serialize.drone(drone)
    db.commit()
    return data


@router.post("/{drone_id}/inspections", status_code=201)
def inspection(drone_id: uuid.UUID, body: schemas.InspectionIn, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    check = svc.technician_inspection(db, p, drone_id, body.model_dump())
    data = serialize.health_check(check)
    db.commit()
    return data


@router.post("/{drone_id}/maintenance", status_code=201)
def maintenance(drone_id: uuid.UUID, body: schemas.MaintenanceIn, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    rec = svc.add_maintenance(db, p, drone_id, body.model_dump())
    data = serialize.maintenance(rec)
    db.commit()
    return data
