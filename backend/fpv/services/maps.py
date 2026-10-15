"""Карты-подложки (Map Service).

Администратор загружает растровое изображение карты (PNG, JPEG, WebP) и указывает его
географические границы. Интерфейс подкладывает подходящую карту под схему маршрута;
внешние картографические сервисы не используются — платформа работает в изолированной сети ЦОД.

Формат определяется по содержимому, а не по заявленному типу; SVG и другие форматы с активным
содержимым не принимаются. Изображение хранится в объектном хранилище, контрольная сумма — в БД.
"""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import NotFound, ValidationFailed
from ..models import MapLayer
from . import audit
from .identity import Principal
from .storage import StorageError

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_SIDE_PX = 20000


def sniff_image(data: bytes) -> Tuple[str, Optional[int], Optional[int]]:
    """Тип и размеры изображения по сигнатуре. Неизвестный формат — ошибка."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return "image/png", w, h
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", *_jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", *_webp_size(data)
    raise ValidationFailed("Поддерживаются только изображения PNG, JPEG и WebP", code="unsupported_image")


def _jpeg_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    return None, None


def _webp_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    if chunk == b"VP8 " and len(data) >= 30:
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        w = 1 + (((b[1] & 0x3F) << 8) | b[0])
        h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return w, h
    return None, None


def _check_bounds(north: float, south: float, west: float, east: float) -> None:
    if not (-85 <= south < north <= 85):
        raise ValidationFailed("Широта: юг должен быть меньше севера, допустимо от −85 до 85", code="invalid_bounds")
    if not (-180 <= west < east <= 180):
        raise ValidationFailed("Долгота: запад должен быть меньше востока, допустимо от −180 до 180",
                               code="invalid_bounds")
    # Подложка рассчитана на район работ: слишком большая область искажает проекцию схемы.
    if north - south > 5 or east - west > 10:
        raise ValidationFailed("Слишком большая область карты (не более 5° по широте и 10° по долготе)",
                               code="invalid_bounds")


def serialize(m: MapLayer) -> dict:
    return {"id": str(m.id), "name": m.name, "description": m.description, "content_type": m.content_type,
            "size_bytes": m.size_bytes, "width_px": m.width_px, "height_px": m.height_px,
            "bounds": {"north": m.north, "south": m.south, "west": m.west, "east": m.east},
            "active": m.active, "created_at": m.created_at.isoformat() if m.created_at else None,
            "image_url": f"/api/v1/maps/{m.id}/image?v={m.sha256[:12]}"}


def list_layers(db: Session, p: Principal, *, include_inactive: bool = False) -> List[MapLayer]:
    p.require("map.read")
    stmt = select(MapLayer)
    if not (include_inactive and p.has("map.manage")):
        stmt = stmt.where(MapLayer.active.is_(True))
    return list(db.execute(stmt.order_by(MapLayer.name)).scalars())


def create(db: Session, p: Principal, *, name: str, description: str, north: float, south: float,
           west: float, east: float, data: bytes) -> MapLayer:
    p.require("map.manage")
    settings = runtime.settings()
    if not data:
        raise ValidationFailed("Файл карты пуст")
    if len(data) > settings.map_max_bytes:
        raise ValidationFailed(f"Файл карты больше {settings.map_max_bytes // (1024 * 1024)} МБ",
                               code="map_too_large")
    content_type, w, h = sniff_image(data)
    if (w and w > MAX_SIDE_PX) or (h and h > MAX_SIDE_PX):
        raise ValidationFailed(f"Размер изображения больше {MAX_SIDE_PX} пикселей по стороне",
                               code="map_too_large")
    _check_bounds(north, south, west, east)
    layer_id = uuid.uuid4()
    key = f"maps/{layer_id}{_EXT[content_type]}"
    fd, tmp = tempfile.mkstemp(prefix="fpv-map-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        runtime.store().put_file(key, Path(tmp), content_type)
    finally:
        Path(tmp).unlink(missing_ok=True)
    now = clock.now()
    layer = MapLayer(id=layer_id, name=name.strip(), description=description.strip(), object_key=key,
                     content_type=content_type, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     width_px=w, height_px=h, north=north, south=south, west=west, east=east, active=True,
                     uploaded_by=p.user_id, created_at=now, updated_at=now)
    db.add(layer)
    db.flush()
    audit.record(db, p.actor, "map.uploaded", entity_type="map", entity_id=layer.id,
                 details={"name": layer.name, "sha256": layer.sha256, "size_bytes": layer.size_bytes,
                          "bounds": [south, west, north, east]})
    return layer


def _get(db: Session, layer_id: uuid.UUID) -> MapLayer:
    layer = db.get(MapLayer, layer_id)
    if layer is None:
        raise NotFound("Карта не найдена")
    return layer


def update(db: Session, p: Principal, layer_id: uuid.UUID, changes: dict) -> MapLayer:
    p.require("map.manage")
    layer = _get(db, layer_id)
    bounds = {k: changes.get(k, getattr(layer, k)) for k in ("north", "south", "west", "east")}
    _check_bounds(**bounds)
    for k, v in changes.items():
        setattr(layer, k, v.strip() if isinstance(v, str) else v)
    layer.updated_at = clock.now()
    audit.record(db, p.actor, "map.updated", entity_type="map", entity_id=layer.id, details=changes)
    return layer


def delete(db: Session, p: Principal, layer_id: uuid.UUID) -> None:
    p.require("map.manage")
    layer = _get(db, layer_id)
    key = layer.object_key
    audit.record(db, p.actor, "map.deleted", entity_type="map", entity_id=layer.id,
                 details={"name": layer.name, "sha256": layer.sha256})
    db.delete(layer)
    db.flush()
    try:
        runtime.store().delete(key)
    except StorageError:     # запись удалена; объект без ссылки не выдаётся
        pass


def image(db: Session, p: Principal, layer_id: uuid.UUID) -> Tuple[Iterator[bytes], MapLayer]:
    p.require("map.read")
    layer = _get(db, layer_id)
    if not layer.active and not p.has("map.manage"):
        raise NotFound("Карта не найдена")
    try:
        return runtime.store().open(layer.object_key), layer
    except StorageError as exc:
        raise NotFound("Файл карты не найден в хранилище") from exc
