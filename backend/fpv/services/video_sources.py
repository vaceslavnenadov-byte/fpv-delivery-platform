"""Источники видео дрона: выбор видеопотока оператором.

Сервисный модуль дрона сообщает, какие источники у него есть (USB-камеры, RTSP-потоки из его
конфигурации, тестовые ролики из его папки, тестовая таблица) и какой сейчас активен. Оператор
выбирает источник из этого списка в мониторе полёта; модуль получает выбор вместе со справкой
о задании и переключает видео. Произвольные адреса и пути оператор не задаёт: только
идентификаторы из списка модуля. Выбор относится к видео и на полёт не влияет.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, ValidationFailed
from ..models import Drone, Order
from . import audit, events
from . import orders as order_svc
from .identity import Principal

KINDS = ("usb", "rtsp", "file", "test")
ID_RE = re.compile(r"^(test|(usb|rtsp|file):[A-Za-z0-9._-]{1,96})$")
MAX_SOURCES = 64
# Пока заказ у оператора и до завершения полёта источник можно менять.
SELECT_STATUSES = ("accepted", "awaiting_readiness", "ready", "in_progress")


def _clean(sources: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Допустимые источники и список пропущенных (неверный формат, повтор). Одна плохая запись
    не должна скрывать остальные источники от оператора и техника."""
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise ValidationFailed("Недопустимый список источников видео")
    out, seen, skipped = [], set(), []
    for s in sources:
        sid = str(s.get("id", "")) if isinstance(s, dict) else ""
        kind = str(s.get("kind", "")) if isinstance(s, dict) else ""
        label = str(s.get("label", ""))[:120] if isinstance(s, dict) else ""
        if not ID_RE.match(sid) or kind not in KINDS or not sid.startswith(kind) or sid in seen:
            skipped.append(sid[:100])
            continue
        seen.add(sid)
        out.append({"id": sid, "kind": kind, "label": label or sid})
    return out, skipped


def _participants(order: Order) -> list:
    return [u for u in (order.operator_id, order.coordinator_id) if u]


def report(db: Session, drone: Drone, sources: List[Dict[str, Any]], active: Any) -> Dict[str, Any]:
    """Сервисный модуль сообщает доступные источники и активный."""
    cleaned, skipped = _clean(sources)
    active_id = str(active) if active else None
    if active_id is not None and active_id not in {s["id"] for s in cleaned}:
        active_id = None                       # активный не прошёл проверку — показываем без отметки
    changed = cleaned != (drone.video_sources or []) or active_id != drone.video_source_active
    drone.video_sources = cleaned
    drone.video_source_active = active_id
    if changed:
        from .telemetry import current_order     # отложенный импорт: telemetry импортирует сервисы заказов
        order = current_order(db, drone, statuses=SELECT_STATUSES)
        events.notify(db, "video_source", {"drone_id": drone.id, "order_id": order.id if order else None},
                      users=_participants(order) if order else [], roles=["admin"])
    return {"selected": drone.video_source_selected, "active": active_id, "accepted": len(cleaned),
            "skipped": skipped}


def _order_drone(db: Session, p: Principal, order_id: uuid.UUID) -> tuple:
    order = order_svc.visible_order(db, p, order_id)
    if order.drone_id is None:
        raise Conflict("К заказу не привязан дрон", code="no_drone")
    drone = db.get(Drone, order.drone_id)
    return order, drone


def can_select(p: Principal, order: Order) -> bool:
    return p.has("order.execute") and order.operator_id == p.user_id and order.status in SELECT_STATUSES


def get(db: Session, p: Principal, order_id: uuid.UUID) -> Dict[str, Any]:
    order, drone = _order_drone(db, p, order_id)
    if order.customer_id == p.user_id and order.operator_id != p.user_id:
        raise Forbidden("Настройки видео дрона доступны исполнителям заказа")
    p.require("video.live")
    return {"sources": drone.video_sources or [], "active": drone.video_source_active,
            "selected": drone.video_source_selected, "can_select": can_select(p, order),
            "link_status": drone.link_status}


def select(db: Session, p: Principal, order_id: uuid.UUID, source_id: str) -> Dict[str, Any]:
    order, drone = _order_drone(db, p, order_id)
    if not can_select(p, order):
        raise Forbidden("Источник видео выбирает назначенный оператор, пока заказ в работе")
    apply_selection(db, p, drone, source_id, entity_type="order", entity_id=order.id,
                    notify_users=_participants(order), order_id=order.id)
    return get(db, p, order_id)


def apply_selection(db: Session, p: Principal, drone: Drone, source_id: str, *, entity_type: str,
                    entity_id: Any, notify_users: Any = (), order_id: Any = None) -> None:
    """Запомнить выбранный источник (оператор — в заказе, техник — при подключении дрона)."""
    if source_id not in {s["id"] for s in drone.video_sources or []}:
        raise ValidationFailed("Такого источника нет у сервисного модуля дрона", code="unknown_video_source")
    previous = drone.video_source_selected
    drone.video_source_selected = source_id
    drone.video_source_selected_by = p.user_id
    drone.video_source_selected_at = clock.now()
    audit.record(db, p.actor, "drone.video_source.selected", entity_type=entity_type, entity_id=entity_id,
                 details={"drone_id": str(drone.id), "from": previous, "to": source_id})
    events.notify(db, "video_source", {"drone_id": drone.id, "order_id": order_id},
                  users=list(notify_users) + [p.user_id], roles=["admin"])
