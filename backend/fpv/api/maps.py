"""REST API карт-подложек и прямой видеотрансляции заказа."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path as PathParam, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from .. import runtime
from ..errors import ValidationFailed
from ..services import live, maps
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(tags=["maps"])


# --- карты ---

@router.get("/api/v1/maps")
def list_maps(all: bool = Query(False), p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [maps.serialize(m) for m in maps.list_layers(db, p, include_inactive=all)]}


@router.get("/api/v1/maps/{map_id}/image")
def map_image(map_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    body, layer = maps.image(db, p, map_id)
    # Содержимое неизменно для данного id (новая карта — новый id), кеширование безопасно.
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'"}
    return StreamingResponse(body, media_type=layer.content_type, headers=headers)


@router.post("/api/v1/admin/maps", status_code=201)
async def upload_map(request: Request,
                     name: str = Query(..., min_length=1, max_length=120),
                     north: float = Query(...), south: float = Query(...),
                     west: float = Query(...), east: float = Query(...),
                     description: str = Query("", max_length=1000),
                     p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Тело запроса — файл изображения (PNG, JPEG или WebP), параметры карты — в строке запроса."""
    p.require("map.manage")
    limit = runtime.settings().map_max_bytes
    if int(request.headers.get("content-length") or 0) > limit:
        raise ValidationFailed(f"Файл карты больше {limit // (1024 * 1024)} МБ", code="map_too_large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValidationFailed(f"Файл карты больше {limit // (1024 * 1024)} МБ", code="map_too_large")

    def store():
        layer = maps.create(db, p, name=name, description=description, north=north, south=south,
                            west=west, east=east, data=bytes(data))
        db.commit()
        return maps.serialize(layer)
    return await run_in_threadpool(store)


@router.patch("/api/v1/admin/maps/{map_id}")
def update_map(map_id: uuid.UUID, body: schemas.MapUpdateIn, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    layer = maps.update(db, p, map_id, body.model_dump(exclude_unset=True, exclude_none=True))
    db.commit()
    return maps.serialize(layer)


@router.delete("/api/v1/admin/maps/{map_id}", status_code=204)
def delete_map(map_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    maps.delete(db, p, map_id)
    db.commit()


# --- прямая трансляция ---

@router.post("/api/v1/orders/{order_id}/live")
def live_start(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    result = live.start(db, p, order_id)
    db.commit()      # факт начала просмотра фиксируется в аудите
    return result


@router.get("/api/v1/orders/{order_id}/live/playlist.m3u8")
def live_playlist(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    text = live.playlist(db, p, order_id)
    return PlainTextResponse(text, media_type="application/vnd.apple.mpegurl")


@router.get("/api/v1/orders/{order_id}/live/frame.jpg")
def live_frame(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return FileResponse(live.frame(db, p, order_id), media_type="image/jpeg")


@router.get("/api/v1/orders/{order_id}/live/{name}.ts")
def live_segment(order_id: uuid.UUID, name: str = PathParam(..., pattern=r"^\d{13}_\d{1,9}$"),
                 p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    path = live.segment(db, p, order_id, name)
    return FileResponse(path, media_type="video/mp2t")
