"""ORM-отображение таблиц. Схема задаётся SQL-миграциями (database/migrations);
здесь только отображение, без create_all."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (BigInteger, Boolean, Float, ForeignKey, Integer, LargeBinary, Numeric,
                        SmallInteger, String, Text)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class PermissionMatrix(Base):
    __tablename__ = "permission_matrix"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission: Mapped[str] = mapped_column(ForeignKey("permissions.code"), primary_key=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_object_id: Mapped[str] = mapped_column(Text, unique=True)
    domain_username: Mapped[str] = mapped_column(Text, unique=True)
    domain_upn: Mapped[Optional[str]] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    directory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    last_directory_sync_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    role: Mapped[Role] = relationship(lazy="joined")


class DomainGroup(Base):
    __tablename__ = "domain_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dn: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roles.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    role: Mapped[Optional[Role]] = relationship(lazy="joined")


class UserDomainGroup(Base):
    __tablename__ = "user_domain_groups"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    domain_group_id: Mapped[int] = mapped_column(ForeignKey("domain_groups.id"), primary_key=True)
    synced_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    hostname: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    domain_joined: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class DeviceAccessPolicy(Base):
    __tablename__ = "device_access_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roles.id"))
    domain_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("domain_groups.id"))
    access_level: Mapped[str] = mapped_column(Text, default="use")
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary)
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("devices.id"))
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    last_directory_check_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    revoke_reason: Mapped[Optional[str]] = mapped_column(Text)


class Operator(Base):
    __tablename__ = "operators"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    license_number: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    completed_orders: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Drone(Base):
    __tablename__ = "drones"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    serial_number: Mapped[str] = mapped_column(Text, unique=True)
    registration_number: Mapped[str] = mapped_column(Text, unique=True)
    manufacturer: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    adapter_kind: Mapped[str] = mapped_column(Text, default="simulator")
    max_payload_g: Mapped[int] = mapped_column(Integer)
    max_range_m: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="pending_approval")
    health_status: Mapped[str] = mapped_column(Text, default="requires_check")
    link_status: Mapped[str] = mapped_column(Text, default="offline")
    last_seen_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    battery_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)
    firmware_version: Mapped[Optional[str]] = mapped_column(Text)
    api_key_hash: Mapped[Optional[bytes]] = mapped_column(LargeBinary, unique=True)
    api_key_prefix: Mapped[Optional[str]] = mapped_column(Text)
    api_key_issued_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Источники видео (сообщает сервисный модуль) и выбор оператора — см. services/video_sources.py
    video_sources: Mapped[Any] = mapped_column(JSONB, default=list)
    video_source_active: Mapped[Optional[str]] = mapped_column(Text)
    video_source_selected: Mapped[Optional[str]] = mapped_column(Text)
    video_source_selected_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    video_source_selected_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class DroneHealthCheck(Base):
    __tablename__ = "drone_health_checks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id"))
    source: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    battery_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)
    error_codes: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    details: Mapped[Any] = mapped_column(JSONB, default=dict)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id"))
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    components: Mapped[Any] = mapped_column(JSONB, default=list)
    health_status_set: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[int] = mapped_column(BigInteger, server_default="nextval('orders_number_seq')")
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id"))
    operator_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    coordinator_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(Text, default="draft")
    status_before_dispute: Mapped[Optional[str]] = mapped_column(Text)
    cargo_type: Mapped[str] = mapped_column(Text)
    cargo_description: Mapped[str] = mapped_column(Text)
    cargo_weight_g: Mapped[int] = mapped_column(Integer)
    cargo_length_mm: Mapped[int] = mapped_column(Integer)
    cargo_width_mm: Mapped[int] = mapped_column(Integer)
    cargo_height_mm: Mapped[int] = mapped_column(Integer)
    recipient_name: Mapped[str] = mapped_column(Text)
    recipient_phone: Mapped[str] = mapped_column(Text)
    window_start: Mapped[dt.datetime] = mapped_column(TS)
    window_end: Mapped[dt.datetime] = mapped_column(TS)
    quote_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    quote_currency: Mapped[str] = mapped_column(Text, default="RUB")
    validation_result: Mapped[Any] = mapped_column(JSONB, default=dict)
    receipt_overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    accepted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    delivered_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    closed_reason: Mapped[Optional[str]] = mapped_column(Text)

    route: Mapped[Optional["Route"]] = relationship(back_populates="order", uselist=False, lazy="joined")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    from_status: Mapped[Optional[str]] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    origin_address: Mapped[str] = mapped_column(Text)
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    dest_address: Mapped[str] = mapped_column(Text)
    distance_m: Mapped[int] = mapped_column(Integer)
    max_altitude_m: Mapped[int] = mapped_column(Integer, default=120)
    checkpoints: Mapped[Any] = mapped_column(JSONB, default=list)
    approval_status: Mapped[str] = mapped_column(Text, default="pending")
    approval_notes: Mapped[Any] = mapped_column(JSONB, default=list)
    approved_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    order: Mapped[Order] = relationship(back_populates="route")


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class OrderNote(Base):
    __tablename__ = "order_notes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, default="info")
    body: Mapped[str] = mapped_column(Text)
    attachment_key: Mapped[Optional[str]] = mapped_column(Text)
    attachment_type: Mapped[Optional[str]] = mapped_column(Text)
    attachment_sha256: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id"))
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    recorded_at: Mapped[dt.datetime] = mapped_column(TS)
    received_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lon: Mapped[Optional[float]] = mapped_column(Float)
    alt_m: Mapped[Optional[float]] = mapped_column(Float)
    ground_speed_mps: Mapped[Optional[float]] = mapped_column(Float)
    heading_deg: Mapped[Optional[float]] = mapped_column(Float)
    battery_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)
    link_quality: Mapped[Optional[int]] = mapped_column(SmallInteger)
    gnss_fix: Mapped[Optional[bool]] = mapped_column(Boolean)
    flight_phase: Mapped[Optional[str]] = mapped_column(Text)
    extra: Mapped[Any] = mapped_column(JSONB, default=dict)


class VideoEvidence(Base):
    __tablename__ = "video_evidence"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    status: Mapped[str] = mapped_column(Text)
    missing_reason: Mapped[Optional[str]] = mapped_column(Text)
    object_key: Mapped[Optional[str]] = mapped_column(Text)
    preview_object_key: Mapped[Optional[str]] = mapped_column(Text)
    sha256: Mapped[Optional[str]] = mapped_column(Text)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    content_type: Mapped[Optional[str]] = mapped_column(Text)
    completion_event_at: Mapped[dt.datetime] = mapped_column(TS)
    clip_start_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    clip_end_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    retention_until: Mapped[dt.datetime] = mapped_column(TS)
    review_status: Mapped[str] = mapped_column(Text, default="pending")
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    integrity_checked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    integrity_ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class VideoAccessLog(Base):
    __tablename__ = "video_access_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_evidence.id"))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    details: Mapped[Any] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class DeliveryConfirmation(Base):
    __tablename__ = "delivery_confirmations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    kind: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(Text)
    comment: Mapped[str] = mapped_column(Text, default="")
    report: Mapped[Any] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class ScoringRules(Base):
    __tablename__ = "scoring_rules"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    rules: Mapped[Any] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class ScoreTransaction(Base):
    __tablename__ = "score_transactions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    kind: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[Any] = mapped_column(JSONB, default=dict)
    rule_version: Mapped[Optional[int]] = mapped_column(ForeignKey("scoring_rules.version"))
    reason: Mapped[str] = mapped_column(Text, default="")
    complaint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("complaints.id"))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Complaint(Base):
    __tablename__ = "complaints"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="open")
    description: Mapped[str] = mapped_column(Text)
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    points_adjustment: Mapped[Optional[int]] = mapped_column(Integer)
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, default="info")
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"))
    dedup_key: Mapped[Optional[str]] = mapped_column(Text)
    read_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(TS)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(Text)
    actor_ip: Mapped[Optional[str]] = mapped_column(INET)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text, default="")
    entity_id: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(Text, default="success")
    details: Mapped[Any] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text, unique=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    processed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    route: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[Optional[int]] = mapped_column(Integer)
    response_body: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class MapLayer(Base):
    """Карта-подложка: изображение с географической привязкой по границам (WGS 84)."""
    __tablename__ = "map_layers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    width_px: Mapped[Optional[int]] = mapped_column(Integer)
    height_px: Mapped[Optional[int]] = mapped_column(Integer)
    north: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
