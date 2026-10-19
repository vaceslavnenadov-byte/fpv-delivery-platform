"""REST API справочников: типы грузов и типы БПЛА."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..services import catalogs
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get("/cargo-types")
def cargo_types(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [catalogs.serialize_cargo(c) for c in catalogs.cargo_types(db)]}


@router.post("/cargo-types", status_code=201)
def create_cargo(body: schemas.CargoTypeIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = catalogs.create_cargo(db, p, body.model_dump())
    db.commit()
    return catalogs.serialize_cargo(c)


@router.patch("/cargo-types/{cargo_id}")
def update_cargo(cargo_id: uuid.UUID, body: schemas.CargoTypePatch, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    c = catalogs.update_cargo(db, p, cargo_id, body.model_dump(exclude_none=True))
    db.commit()
    return catalogs.serialize_cargo(c)


@router.delete("/cargo-types/{cargo_id}", status_code=204)
def delete_cargo(cargo_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    catalogs.delete_cargo(db, p, cargo_id)
    db.commit()


@router.get("/drone-models")
def drone_models(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if not (p.has("fleet.manage") or p.has("catalog.manage") or p.has("drone.read_all") or p.has("operator.license")):
        p.require("fleet.manage")
    return {"items": [catalogs.serialize_model(m) for m in catalogs.drone_models(db)]}


@router.post("/drone-models", status_code=201)
def create_model(body: schemas.DroneModelIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = catalogs.create_model(db, p, body.model_dump())
    db.commit()
    return catalogs.serialize_model(m)


@router.patch("/drone-models/{model_id}")
def update_model(model_id: uuid.UUID, body: schemas.DroneModelPatch, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    m = catalogs.update_model(db, p, model_id, body.model_dump(exclude_none=True))
    db.commit()
    return catalogs.serialize_model(m)


@router.delete("/drone-models/{model_id}", status_code=204)
def delete_model(model_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    catalogs.delete_model(db, p, model_id)
    db.commit()


@router.get("/consignees")
def consignees(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [catalogs.serialize_consignee(db, c) for c in catalogs.consignees(db)],
            "priorities": catalogs.PRIORITIES}


@router.post("/consignees", status_code=201)
def create_consignee(body: schemas.ConsigneeIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = catalogs.create_consignee(db, p, body.model_dump())
    data = catalogs.serialize_consignee(db, c)
    db.commit()
    return data


@router.patch("/consignees/{consignee_id}")
def update_consignee(consignee_id: uuid.UUID, body: schemas.ConsigneePatch, p: Principal = Depends(current_principal),
                     db: Session = Depends(get_db)):
    c = catalogs.update_consignee(db, p, consignee_id, body.model_dump(exclude_unset=True))
    data = catalogs.serialize_consignee(db, c)
    db.commit()
    return data


@router.delete("/consignees/{consignee_id}", status_code=204)
def delete_consignee(consignee_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    catalogs.delete_consignee(db, p, consignee_id)
    db.commit()
