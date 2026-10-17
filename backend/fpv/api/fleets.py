"""REST API парков дронов: техник ведёт свой парк, заказчик выбирает дрон из открытых."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path as PathParam
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import ServiceUnavailable
from ..models import Drone, Fleet
from ..services import orders as orders_svc
from ..services import audit, fleets, live
from ..services.identity import Principal
from . import schemas, serialize
from .deps import current_principal, get_db

router = APIRouter(tags=["fleets"])


def fleet_json(f: Fleet, drone_count: int = 0) -> dict:
    return {"id": str(f.id), "name": f.name, "technician_id": str(f.technician_id),
            "base": {"lat": f.base_lat, "lon": f.base_lon, "address": f.base_address},
            "active": f.active, "drone_count": drone_count, "created_at": serialize.ts(f.created_at),
            "zone_id": str(f.zone_id) if f.zone_id else None}


@router.get("/api/v1/fleets")
def list_fleets(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    items = fleets.list_fleets(db, p)
    counts = dict(db.execute(select(Drone.fleet_id, func.count()).where(Drone.fleet_id.in_([f.id for f in items]),
                                                                       Drone.archived_at.is_(None))
                             .group_by(Drone.fleet_id)).all()) if items else {}
    return {"items": [fleet_json(f, counts.get(f.id, 0)) for f in items]}


@router.post("/api/v1/fleets", status_code=201)
def create_fleet(body: schemas.FleetIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    fleet = fleets.create_fleet(db, p, body.model_dump())
    data = fleet_json(fleet)
    db.commit()
    return data


@router.patch("/api/v1/fleets/{fleet_id}")
def update_fleet(fleet_id: uuid.UUID, body: schemas.FleetUpdateIn, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    fleet = fleets.update_fleet(db, p, fleet_id, body.model_dump())
    data = fleet_json(fleet)
    db.commit()
    return data


@router.get("/api/v1/fleets/{fleet_id}/drones")
def fleet_drones(fleet_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Дроны парка («Мой парк»); перенесённые в архив — в разделе «Архив БПЛА»."""
    fleet = fleets.own_fleet(db, p, fleet_id)
    rows = db.execute(select(Drone).where(Drone.fleet_id == fleet.id, Drone.archived_at.is_(None))
                      .order_by(Drone.created_at)).scalars()
    return {"fleet": fleet_json(fleet), "items": [serialize.drone(d) for d in rows]}


@router.get("/api/v1/fleet-orders")
def fleet_orders(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [serialize.order(db, o, p, brief=True) for o in fleets.fleet_orders(db, p)]}


@router.get("/api/v1/available-drones")
def available_drones(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Дроны, открытые техниками для заказов: только сведения, нужные для выбора."""
    return {"items": [{
        "id": str(d.id), "registration_number": d.registration_number, "model": d.model,
        "manufacturer": d.manufacturer, "max_payload_g": d.max_payload_g, "max_range_m": d.max_range_m,
        "link_status": d.link_status, "battery_pct": d.battery_pct, "busy": busy,
        "fleet": {"id": str(f.id), "name": f.name, "zone_id": str(f.zone_id) if f.zone_id else None,
                  "base": {"lat": f.base_lat, "lon": f.base_lon, "address": f.base_address}},
    } for d, f, busy in fleets.available_drones(db, p)]}


# --- архив БПЛА и отчёт о техсостоянии ---

def _archive_row(d: Drone, f: Fleet, last=None) -> dict:
    return {**serialize.drone(d), "fleet_name": f.name, "last_flight_at": serialize.ts(last)}


@router.get("/api/v1/drone-archive")
def drone_archive(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [_archive_row(d, f) for d, f in fleets.technician_drones(db, p, archived=True)],
            "reasons": fleets.ARCHIVE_REASONS}


@router.post("/api/v1/drones/{drone_id}/archive")
def archive(drone_id: uuid.UUID, body: schemas.ArchiveIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
    drone = fleets.archive_drone(db, p, drone_id, body.reason, body.comment)
    data = serialize.drone(drone)
    db.commit()
    return data


@router.delete("/api/v1/drones/{drone_id}/archive")
def unarchive(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = fleets.unarchive_drone(db, p, drone_id)
    data = serialize.drone(drone)
    db.commit()
    return data


@router.get("/api/v1/tech-report")
def tech_report(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Отчёт о техсостоянии всех дронов техника: сначала работоспособные, архивные — в конце."""
    rows = fleets.technician_drones(db, p)
    last = fleets.last_flights(db, [d.id for d, _ in rows])
    order = {"serviceable": 0, "requires_check": 1, "grounded": 2}
    rows.sort(key=lambda r: (r[0].archived_at is not None, order.get(r[0].health_status, 3),
                             r[0].registration_number))
    from ..services import people
    return {"items": [_archive_row(d, f, last.get(d.id)) for d, f in rows], "reasons": fleets.ARCHIVE_REASONS,
            "technician": people.report_identity(db, p.user_id), "generated_at": serialize.ts(clock.now())}


# --- дрон парка: публикация, перевод в парк, подключение, проверка видео ---

@router.post("/api/v1/drones/{drone_id}/publish")
def publish(drone_id: uuid.UUID, body: schemas.PublishIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
    drone = fleets.set_published(db, p, drone_id, body.published)
    data = serialize.drone(drone)
    db.commit()
    return data


@router.post("/api/v1/drones/{drone_id}/fleet")
def assign_fleet(drone_id: uuid.UUID, body: schemas.FleetAssignIn, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    drone = fleets.assign_to_fleet(db, p, drone_id, body.fleet_id)
    data = serialize.drone(drone)
    db.commit()
    return data


@router.get("/api/v1/drones/{drone_id}/connection")
def connection(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return fleets.connection(db, runtime.settings(), p, drone_id)


@router.post("/api/v1/drones/{drone_id}/video-check")
def video_check_start(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    fleets.start_video_check(db, p, drone_id)
    db.commit()
    return fleets.connection(db, runtime.settings(), p, drone_id)


@router.delete("/api/v1/drones/{drone_id}/video-check")
def video_check_stop(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    fleets.stop_video_check(db, runtime.settings(), p, drone_id)
    db.commit()
    return fleets.connection(db, runtime.settings(), p, drone_id)


@router.put("/api/v1/drones/{drone_id}/video-source")
def drone_video_source(drone_id: uuid.UUID, body: schemas.VideoSourceSelectIn,
                       p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    fleets.select_video_source(db, p, drone_id, body.source_id)
    db.commit()
    return fleets.connection(db, runtime.settings(), p, drone_id)


# Пробная трансляция (проверка видео техником до заказа) — тот же конвейер HLS, буфер дрона.

def _check_drone(db: Session, p: Principal, drone_id: uuid.UUID) -> Drone:
    return fleets.fleet_drone(db, p, drone_id)


@router.post("/api/v1/drones/{drone_id}/live/check")
def check_live_start(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = _check_drone(db, p, drone_id)
    if not live._ffmpeg(runtime.settings()):
        raise ServiceUnavailable("Трансляция недоступна: на сервере не установлен ffmpeg", code="live_unavailable")
    audit.record(db, p.actor, "video.live.view", entity_type="drone", entity_id=drone.id,
                 details={"source": fleets.CHECK_SOURCE})
    db.commit()
    base = f"/api/v1/drones/{drone.id}/live/check"
    return {"playlist_url": f"{base}/playlist.m3u8", "frame_url": f"{base}/frame.jpg"}


@router.get("/api/v1/drones/{drone_id}/live/check/playlist.m3u8")
def check_live_playlist(drone_id: uuid.UUID, p: Principal = Depends(current_principal),
                        db: Session = Depends(get_db)):
    drone = _check_drone(db, p, drone_id)
    text = live.build_playlist(runtime.settings(), drone.id, fleets.CHECK_SOURCE)
    return PlainTextResponse(text, media_type="application/vnd.apple.mpegurl")


@router.get("/api/v1/drones/{drone_id}/live/check/frame.jpg")
def check_live_frame(drone_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = _check_drone(db, p, drone_id)
    return FileResponse(live.frame_path(runtime.settings(), drone.id, fleets.CHECK_SOURCE), media_type="image/jpeg")


@router.get("/api/v1/drones/{drone_id}/live/check/{name}.ts")
def check_live_segment(drone_id: uuid.UUID, name: str = PathParam(..., pattern=r"^\d{13}_\d{1,9}$"),
                       p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    drone = _check_drone(db, p, drone_id)
    return FileResponse(live.segment_path(runtime.settings(), drone.id, name, fleets.CHECK_SOURCE),
                        media_type="video/mp2t")


# --- первичная готовность дрона к заказу (техник парка) ---

@router.post("/api/v1/orders/{order_id}/tech-ready")
def tech_ready(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order, reasons = fleets.confirm_order_ready(db, runtime.settings(), p, order_id)
    data = {"ready": not reasons, "reasons": reasons, "order": serialize.order(db, order, p)}
    db.commit()
    return data


@router.post("/api/v1/orders/{order_id}/tech-ready/revoke")
def tech_ready_revoke(order_id: uuid.UUID, body: schemas.RevokeIn, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db)):
    order = fleets.revoke_order_ready(db, p, order_id, body.reason)
    data = serialize.order(db, order, p)
    db.commit()
    return data


# --- видеостена координатора: назначенные ему заказы (до coordinator_max_orders) ---

@router.get("/api/v1/coordinator/wall")
def coordinator_wall(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    from ..models import Order, TelemetryRecord
    from ..services import coordination, drones as drone_svc
    p.require("order.note.write")
    settings = runtime.settings()
    statuses = coordination.ACTIVE + ("awaiting_delivery_confirmation",)
    orders = list(db.execute(select(Order).where(Order.coordinator_id == p.user_id, Order.status.in_(statuses))
                             .order_by(orders_svc.PRIORITY_FIRST, Order.accepted_at)).unique().scalars())
    orders.sort(key=lambda o: o.status != "in_progress")     # в полёте — первыми
    items = []
    for o in orders[:settings.coordinator_max_orders]:
        d = db.get(Drone, o.drone_id)
        t = None
        if o.started_at is not None:
            t = db.execute(select(TelemetryRecord).where(TelemetryRecord.order_id == o.id)
                           .order_by(TelemetryRecord.recorded_at.desc()).limit(1)).scalar_one_or_none()
        items.append({**serialize.order(db, o, p, brief=True),
                      "drone": {"registration_number": d.registration_number if d else None,
                                "link_status": drone_svc.compute_link_status(settings, d) if d else "offline",
                                "battery_pct": d.battery_pct if d else None},
                      "telemetry": serialize.telemetry(t) if t else None})
    active = sum(1 for o in orders if o.status in coordination.ACTIVE)
    return {"items": items, "load": active, "capacity": settings.coordinator_max_orders,
            "total": len(orders)}
