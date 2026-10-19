"""Преобразование сущностей в ответы API с учётом прав получателя."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Drone, DroneHealthCheck, MaintenanceRecord, Order, TelemetryRecord, User,
                      VideoEvidence)
from ..services.identity import Principal
from ..services.notifications import STATUS_LABELS


def ts(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def user_brief(db: Session, user_id) -> Optional[Dict[str, Any]]:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    return {"id": str(u.id), "display_name": u.display_name, "username": u.domain_username} if u else None


def drone(d: Drone, *, full: bool = True) -> Dict[str, Any]:
    data = {
        "id": str(d.id), "serial_number": d.serial_number, "registration_number": d.registration_number,
        "manufacturer": d.manufacturer, "model": d.model, "adapter_kind": d.adapter_kind,
        "max_payload_g": d.max_payload_g, "max_range_m": d.max_range_m, "status": d.status,
        "health_status": d.health_status, "link_status": d.link_status, "last_seen_at": ts(d.last_seen_at),
        "battery_pct": d.battery_pct, "firmware_version": d.firmware_version,
        "has_key": d.api_key_hash is not None, "key_prefix": d.api_key_prefix,
        "key_issued_at": ts(d.api_key_issued_at), "blocked_reason": d.blocked_reason,
        "owner_id": str(d.owner_id), "created_at": ts(d.created_at),
        "fleet_id": str(d.fleet_id) if d.fleet_id else None, "published": bool(d.published),
        "video_checked_at": ts(d.video_checked_at),
        "drone_model_id": str(d.drone_model_id) if d.drone_model_id else None,
        "link_type": d.link_type, "construction": d.construction, "engine_type": d.engine_type,
        "archived": d.archived_at is not None, "archived_at": ts(d.archived_at),
        "archive_reason": d.archive_reason, "archive_comment": d.archive_comment,
    }
    return data


def health_check(c: DroneHealthCheck) -> Dict[str, Any]:
    return {"id": c.id, "source": c.source, "result": c.result, "battery_pct": c.battery_pct,
            "error_codes": c.error_codes, "details": c.details, "created_at": ts(c.created_at),
            "created_by": str(c.created_by) if c.created_by else None}


def maintenance(m: MaintenanceRecord) -> Dict[str, Any]:
    return {"id": m.id, "kind": m.kind, "description": m.description, "components": m.components,
            "health_status_set": m.health_status_set, "technician_id": str(m.technician_id),
            "created_at": ts(m.created_at)}


def order(db: Session, o: Order, p: Optional[Principal] = None, *, brief: bool = False) -> Dict[str, Any]:
    r = o.route
    data: Dict[str, Any] = {
        "id": str(o.id), "number": o.number, "status": o.status, "status_label": STATUS_LABELS.get(o.status),
        "version": o.version, "cargo_type": o.cargo_type, "cargo_description": o.cargo_description,
        "cargo_type_name": _cargo_names(db).get(o.cargo_type, o.cargo_type),
        "cargo_weight_g": o.cargo_weight_g,
        "cargo_dims_mm": [o.cargo_length_mm, o.cargo_width_mm, o.cargo_height_mm],
        "window_start": ts(o.window_start), "window_end": ts(o.window_end),
        "quote_amount": str(o.quote_amount) if o.quote_amount is not None else None,
        "quote_currency": o.quote_currency,
        "drone_id": str(o.drone_id), "customer_id": str(o.customer_id),
        "operator_id": str(o.operator_id) if o.operator_id else None,
        "coordinator_id": str(o.coordinator_id) if o.coordinator_id else None,
        "created_at": ts(o.created_at), "updated_at": ts(o.updated_at), "submitted_at": ts(o.submitted_at),
        "accepted_at": ts(o.accepted_at), "started_at": ts(o.started_at), "delivered_at": ts(o.delivered_at),
        "completed_at": ts(o.completed_at), "closed_reason": o.closed_reason, "receipt_overdue": o.receipt_overdue,
        "route": {
            "origin": {"lat": r.origin_lat, "lon": r.origin_lon, "address": r.origin_address},
            "destination": {"lat": r.dest_lat, "lon": r.dest_lon, "address": r.dest_address},
            "base": {"lat": r.base_lat, "lon": r.base_lon, "address": r.base_address}
                    if r.base_lat is not None else None,
            "return_m": r.return_m, "stops": r.stops or [], "pickup_at_base": bool(r.pickup_at_base),
            "distance_m": r.distance_m, "max_altitude_m": r.max_altitude_m, "checkpoints": r.checkpoints,
            "approval_status": r.approval_status, "approval_notes": r.approval_notes,
        } if r else None,
        "tech_ready_at": ts(o.tech_ready_at),
        "map_provider_id": str(o.map_provider_id) if o.map_provider_id else None,
        "priority": bool(o.priority),
        "priority_level": o.priority_level,
        "consignee": o.consignee, "recipient_name": o.recipient_name,
        "asap": bool(o.asap), "coordinator_approved_at": ts(o.coordinator_approved_at),
        "repeat_of": _repeat_of(db, o),
    }
    if brief:
        return data
    from ..services import orders as orders_svc
    items = orders_svc.cargo_items(db, o)
    names = _cargo_names(db)
    data["cargo_items"] = [{"position": i.position, "cargo_type": i.cargo_type,
                            "cargo_type_name": names.get(i.cargo_type, i.cargo_type), "description": i.description,
                            "weight_g": i.weight_g, "dims_mm": [i.length_mm, i.width_mm, i.height_mm]}
                           for i in items]
    data["validation_result"] = o.validation_result
    data["customer_comment"] = o.customer_comment
    data["has_comment_photo"] = bool(o.comment_photo_key)
    data["recipient_phone"] = o.recipient_phone
    data["customer"] = user_brief(db, o.customer_id)
    data["operator"] = user_brief(db, o.operator_id)
    data["coordinator"] = user_brief(db, o.coordinator_id)
    data["tech_ready_by"] = user_brief(db, o.tech_ready_by)
    d = db.get(Drone, o.drone_id)
    data["drone"] = drone(d) if d else None
    ev = db.execute(select(VideoEvidence).where(VideoEvidence.order_id == o.id)).scalar_one_or_none()
    if ev:
        from ..services import video as video_svc
        data["video"] = video(ev, p, full=p is not None and video_svc.can_read(db, p, o.id))
    else:
        data["video"] = None
    from ..services import complaints
    c = complaints.latest_dispute(db, o.id)
    data["dispute"] = complaint(db, c) if c else None
    from ..services import repeats
    data["repeat_requests"] = [repeats.serialize(db, r) for r in repeats.for_order(db, o.id)]
    return data


def _cargo_names(db: Session) -> Dict[str, str]:
    """Названия типов грузов (кэш на время запроса)."""
    cache = db.info.get("cargo_names")
    if cache is None:
        from ..services import catalogs
        cache = db.info["cargo_names"] = catalogs.cargo_names(db)
    return cache


def _repeat_of(db: Session, o: Order) -> Optional[Dict[str, Any]]:
    if not o.repeat_of:
        return None
    src = db.get(Order, o.repeat_of)
    return {"id": str(o.repeat_of), "number": src.number if src else None}


def complaint(db: Session, c: Any) -> Dict[str, Any]:
    return {"id": c.id, "order_id": str(c.order_id), "kind": c.kind, "status": c.status,
            "description": c.description, "resolution": c.resolution, "points_adjustment": c.points_adjustment,
            "author": user_brief(db, c.author_id), "created_at": ts(c.created_at),
            "resolved_at": ts(c.resolved_at), "resolved_by": user_brief(db, c.resolved_by),
            "coordinator_verdict": c.coordinator_verdict, "coordinator_comment": c.coordinator_comment,
            "coordinator": user_brief(db, c.coordinator_id), "coordinator_at": ts(c.coordinator_at)}


def video(ev: VideoEvidence, p: Optional[Principal] = None, *, full: Optional[bool] = None) -> Dict[str, Any]:
    data = {"id": str(ev.id), "status": ev.status, "missing_reason": ev.missing_reason,
            "completion_event_at": ts(ev.completion_event_at), "clip_start_at": ts(ev.clip_start_at),
            "clip_end_at": ts(ev.clip_end_at), "segment_count": ev.segment_count,
            "review_status": ev.review_status, "reviewed_at": ts(ev.reviewed_at),
            "review_comment": ev.review_comment, "integrity_ok": ev.integrity_ok,
            "integrity_checked_at": ts(ev.integrity_checked_at), "retention_until": ts(ev.retention_until)}
    if full if full is not None else (p is not None and p.has("video.read")):
        data.update({"sha256": ev.sha256, "size_bytes": ev.size_bytes, "has_preview": bool(ev.preview_object_key),
                     "object_key": ev.object_key})
    return data


def telemetry(t: TelemetryRecord) -> Dict[str, Any]:
    return {"seq": t.seq, "recorded_at": ts(t.recorded_at), "lat": t.lat, "lon": t.lon, "alt_m": t.alt_m,
            "ground_speed_mps": t.ground_speed_mps, "heading_deg": t.heading_deg, "battery_pct": t.battery_pct,
            "link_quality": t.link_quality, "gnss_fix": t.gnss_fix, "flight_phase": t.flight_phase,
            "extra": {k: v for k, v in (t.extra or {}).items() if k in TELEMETRY_EXTRA}}


# Дополнительные показатели сервисного модуля, которые показываются в мониторе полёта.
TELEMETRY_EXTRA = ("satellites", "voltage_v", "alt_msl_m", "vspeed_mps", "wind_mps", "wind_dir_deg",
                   "pitch_deg", "roll_deg", "flight_mode", "waypoint_index", "waypoint_total",
                   "video_resolution", "video_fps")
