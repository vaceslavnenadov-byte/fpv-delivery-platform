"""Трансляция с камеры заказчика (из браузера: телефон или компьютер).

Заказчик включает камеру в карточке своего заказа, пока заказ в работе у оператора (от принятия
до подтверждения доставки), например чтобы показать место приёма груза. Браузер записывает
короткие самостоятельные сегменты (WebM или MP4, по ~2 с) и отправляет их сюда; дальше
используется тот же конвейер, что для камеры дрона (``services/live.py``, источник ``customer``):
перекодирование в H.264, HLS, режим кадров.

Видео заказчика — не доказательная база и не хранится: кольцевой буфер держит последние
``KEEP_S`` секунд, удаляется при остановке трансляции, а фоновая задача удаляет буферы заказов,
которые больше не в работе или по которым трансляция прекратилась.

Смотрят: назначенные оператор и координатор, администратор (``video.live`` в пределах доступа
к заказу). Начало и остановка трансляции записываются в аудит, исполнители получают сигнал.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from .. import runtime
from ..config import Settings
from ..errors import Conflict, Forbidden, ValidationFailed
from ..models import Order
from . import audit, events, live, video
from . import orders as order_svc
from .identity import Principal

SOURCE = "customer"
KEEP_S = 30                     # хранится не больше 30 секунд видео
SESSION_GAP_S = 15              # пауза дольше — новая трансляция (запись в аудит, сигнал исполнителям)
STALE_S = 60                    # буфер без новых сегментов дольше — удаляется фоновой задачей
CONTENT_TYPES = ("video/webm", "video/mp4", "video/x-matroska")


def _check_container(data: bytes, content_type: str) -> None:
    """Сигнатура контейнера должна соответствовать заявленному типу."""
    if content_type in ("video/webm", "video/x-matroska"):
        ok = data[:4] == b"\x1a\x45\xdf\xa3"
    else:
        ok = data[4:8] in (b"ftyp", b"moof", b"styp")
    if not ok:
        raise ValidationFailed("Содержимое не соответствует формату видеосегмента", code="invalid_segment")


def _participants(order: Order) -> list:
    return [u for u in (order.operator_id, order.coordinator_id) if u]


def publish(db: Session, p: Principal, order_id: uuid.UUID, seq: int, duration_ms: int, data: bytes,
            content_type: str) -> Dict[str, Any]:
    p.require("video.publish")
    settings = runtime.settings()
    order = order_svc.visible_order(db, p, order_id)
    if order.customer_id != p.user_id:
        raise Forbidden("Транслировать может только заказчик")
    if order.status not in live.ACTIVE_STATUSES[SOURCE]:
        raise Conflict("Трансляция доступна, пока заказ в работе у оператора", code="stream_not_allowed")
    if content_type not in CONTENT_TYPES:
        raise ValidationFailed("Неподдерживаемый формат видео (нужен WebM или MP4)")
    if not data or len(data) > settings.video_max_segment_bytes:
        raise ValidationFailed("Недопустимый размер видеосегмента")
    if not (200 <= duration_ms <= 10000) or not (0 <= seq < 10**9):
        raise ValidationFailed("Недопустимые параметры видеосегмента")
    _check_container(data, content_type)
    state = video.buffer_state(settings, order.id, SOURCE)
    new_session = state["last_segment_age_s"] is None or state["last_segment_age_s"] > SESSION_GAP_S
    result = video.write_segment(settings, order.id, SOURCE, seq, duration_ms, data, keep_s=KEEP_S)
    if new_session and result["accepted"]:
        audit.record(db, p.actor, "video.customer_stream.started", entity_type="order", entity_id=order.id)
        events.notify(db, "customer_stream", {"order_id": order.id, "active": True},
                      users=_participants(order), roles=["admin"])
    return result


def stop(db: Session, p: Principal, order_id: uuid.UUID) -> None:
    p.require("video.publish")
    order = order_svc.visible_order(db, p, order_id)
    if order.customer_id != p.user_id:
        raise Forbidden("Транслировать может только заказчик")
    _remove(runtime.settings(), order.id)
    audit.record(db, p.actor, "video.customer_stream.stopped", entity_type="order", entity_id=order.id)
    events.notify(db, "customer_stream", {"order_id": order.id, "active": False},
                  users=_participants(order), roles=["admin"])


def status(db: Session, p: Principal, order_id: uuid.UUID) -> Dict[str, Any]:
    order = order_svc.visible_order(db, p, order_id)
    if order.customer_id != p.user_id:
        p.require("video.live")
    state = video.buffer_state(runtime.settings(), order.id, SOURCE)
    age: Optional[float] = state["last_segment_age_s"]
    return {"active": order.status in live.ACTIVE_STATUSES[SOURCE] and age is not None and age <= 6,
            "last_segment_age_s": age, "segments": state["segments"]}


def _remove(settings: Settings, order_id: uuid.UUID) -> None:
    shutil.rmtree(video.ring_dir(settings, order_id, SOURCE).parent, ignore_errors=True)


def sweep(db: Session, settings: Settings) -> int:
    """Удаление буферов заказов, которые больше не в работе, и прекратившихся трансляций."""
    root = Path(settings.video_spool_dir)
    removed = 0
    if not root.is_dir():
        return 0
    for order_dir in root.iterdir():
        if not (order_dir / SOURCE).is_dir():
            continue
        try:
            order_id = uuid.UUID(order_dir.name)
        except ValueError:
            continue
        order = db.get(Order, order_id)
        age = video.buffer_state(settings, order_id, SOURCE)["last_segment_age_s"]
        if order is None or order.status not in live.ACTIVE_STATUSES[SOURCE] or age is None or age > STALE_S:
            _remove(settings, order_id)
            removed += 1
    return removed
