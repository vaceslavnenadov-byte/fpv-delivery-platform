"""Контур сервисного модуля дрона (отдельный от пользовательского API).

Аутентификация — ключ устройства (заголовок ``Authorization: Drone <ключ>``).
Контур принимает телеметрию, диагностику, события и видеосегменты и выдаёт
справочную информацию о задании. Команды управления полётом через этот
контур (и через API заказов) не передаются.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .. import runtime
from ..errors import Conflict, ValidationFailed
from ..models import Drone
from ..services import fleets, telemetry, video, video_sources
from . import schemas
from .deps import current_drone, get_db

router = APIRouter(prefix="/drone-link/v1", tags=["drone-link"])


@router.get("/ping")
def ping(drone: Drone = Depends(current_drone), db: Session = Depends(get_db)):
    telemetry.mark_seen(db, runtime.settings(), drone)
    db.commit()
    return {"drone_id": str(drone.id), "status": drone.status, "health_status": drone.health_status}


@router.get("/mission")
def mission(drone: Drone = Depends(current_drone), db: Session = Depends(get_db)):
    info = telemetry.mission_info(db, drone)
    info["video_buffer_seconds"] = runtime.settings().video_buffer_seconds
    telemetry.mark_seen(db, runtime.settings(), drone)
    db.commit()
    return info


@router.post("/telemetry")
def ingest(body: schemas.TelemetryBatchIn, drone: Drone = Depends(current_drone), db: Session = Depends(get_db)):
    result = telemetry.ingest(db, runtime.settings(), drone, [r.model_dump() for r in body.records])
    db.commit()
    return result


@router.post("/video/sources")
def report_video_sources(body: schemas.VideoSourcesIn, drone: Drone = Depends(current_drone),
                         db: Session = Depends(get_db)):
    result = video_sources.report(db, drone, [s.model_dump() for s in body.sources], body.active)
    telemetry.mark_seen(db, runtime.settings(), drone)
    db.commit()
    return result


@router.post("/events")
def device_event(body: schemas.DeviceEventIn, drone: Drone = Depends(current_drone), db: Session = Depends(get_db)):
    result = telemetry.handle_device_event(db, runtime.settings(), drone, body.kind, body.data)
    db.commit()
    return result


@router.post("/video/segments")
async def video_segment(request: Request, drone: Drone = Depends(current_drone), db: Session = Depends(get_db),
                        x_segment_seq: int = Header(...), x_segment_duration_ms: int = Header(...),
                        content_type: str = Header(default="video/mp2t")):
    settings = runtime.settings()
    length = int(request.headers.get("content-length") or 0)
    if length > settings.video_max_segment_bytes:
        raise ValidationFailed("Сегмент превышает допустимый размер")
    data = await request.body()

    def store_segment():
        order = telemetry.current_order(db, drone)
        if order is None:
            if fleets.check_active(drone):
                # Проверка видео техником до заказа: отдельный короткий буфер дрона.
                result = fleets.accept_check_segment(db, settings, drone, x_segment_seq, x_segment_duration_ms, data,
                                                     content_type.split(";")[0].strip())
                telemetry.mark_seen(db, settings, drone)
                db.commit()
                result["check"] = True
                return result
            raise Conflict("Нет выполняемого заказа для видеопотока", code="no_active_order")
        result = video.add_segment(settings, order, x_segment_seq, x_segment_duration_ms, data,
                                   content_type.split(";")[0].strip())
        telemetry.mark_seen(db, settings, drone)
        db.commit()
        result["order_id"] = str(order.id)
        return result

    # Работа с БД и диском — в пуле потоков, чтобы не блокировать цикл событий.
    return await run_in_threadpool(store_segment)
