"""ORM-отображение таблиц. Схема задаётся SQL-миграциями (database/migrations);
здесь только отображение, без create_all."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (BigInteger, Boolean, Date, Float, ForeignKey, Integer, LargeBinary, Numeric,
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
    profile_status: Mapped[str] = mapped_column(Text, default="pending")
    profile_reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    profile_reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    profile_review_notes: Mapped[Optional[str]] = mapped_column(Text)
    registered_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    auth_source: Mapped[str] = mapped_column(Text, default="domain")          # domain | local
    password_hash: Mapped[Optional[str]] = mapped_column(Text)
    password_changed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    password_changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    status_changed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    status_changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    middle_name: Mapped[Optional[str]] = mapped_column(Text)
    position_title: Mapped[Optional[str]] = mapped_column(Text)
    profile_notes: Mapped[Optional[str]] = mapped_column(Text)
    org_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("org_units.id"))
    nickname: Mapped[Optional[str]] = mapped_column(Text)
    profile_verified_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)   # первое подтверждение профиля
    location: Mapped[Optional[str]] = mapped_column(Text)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    last_directory_sync_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class OrgUnit(Base):
    """Внутренняя группа пользователей (компания, отдел, кабинет …); дерево любой глубины."""
    __tablename__ = "org_units"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("org_units.id"))
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


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


class Fleet(Base):
    """Парк дронов техника; база парка — точка А маршрута."""
    __tablename__ = "fleets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    base_lat: Mapped[float] = mapped_column(Float)
    base_lon: Mapped[float] = mapped_column(Float)
    base_address: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("service_zones.id"))   # зона обслуживания
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


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
    fleet_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("fleets.id"))
    published: Mapped[bool] = mapped_column(Boolean, default=False)       # открыт для выбора заказчиками
    video_check_until: Mapped[Optional[dt.datetime]] = mapped_column(TS)  # проверка видео техником до заказа
    video_checked_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)   # когда в проверке пришло видео
    # Источники видео (сообщает сервисный модуль) и выбор оператора — см. services/video_sources.py
    video_sources: Mapped[Any] = mapped_column(JSONB, default=list)
    video_source_active: Mapped[Optional[str]] = mapped_column(Text)
    video_source_selected: Mapped[Optional[str]] = mapped_column(Text)
    video_source_selected_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    video_source_selected_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    drone_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("drone_models.id"))
    link_type: Mapped[Optional[str]] = mapped_column(Text)
    construction: Mapped[Optional[str]] = mapped_column(Text)
    engine_type: Mapped[Optional[str]] = mapped_column(Text)
    archived_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)            # архив БПЛА
    archived_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    archive_reason: Mapped[Optional[str]] = mapped_column(Text)
    archive_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class Consignee(Base):
    """Грузополучатель из справочника администратора («Типы грузополучателей»)."""
    __tablename__ = "consignees"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="")
    recommended_cargo: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(Text, default="normal")
    comment: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class WorkReadiness(Base):
    """Готовность к работе пользователя в роли; нет записи — готов."""
    __tablename__ = "work_readiness"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    ready: Mapped[bool] = mapped_column(Boolean, default=True)
    changed_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class OperatorLicense(Base):
    """Лицензия оператора на тип БПЛА (квалификация)."""
    __tablename__ = "operator_licenses"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    drone_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("drone_models.id"))
    model: Mapped[Any] = mapped_column(JSONB)
    license_number: Mapped[str] = mapped_column(Text)
    issued_on: Mapped[dt.date] = mapped_column(Date)
    issued_by: Mapped[str] = mapped_column(Text)
    scan_key: Mapped[str] = mapped_column(Text)
    scan_type: Mapped[str] = mapped_column(Text)
    scan_name: Mapped[str] = mapped_column(Text, default="")
    scan_sha256: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class CargoType(Base):
    """Тип груза из справочника администратора."""
    __tablename__ = "cargo_types"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, default="")
    max_weight_g: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class DroneModel(Base):
    """Тип (конфигурация) БПЛА из справочника администратора."""
    __tablename__ = "drone_models"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model: Mapped[str] = mapped_column(Text)
    manufacturer: Mapped[str] = mapped_column(Text)
    link_type: Mapped[str] = mapped_column(Text, default="")
    construction: Mapped[str] = mapped_column(Text, default="")
    engine_type: Mapped[str] = mapped_column(Text, default="")
    max_range_m: Mapped[int] = mapped_column(Integer)
    max_payload_g: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class OrderCargoItem(Base):
    __tablename__ = "order_cargo_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    position: Mapped[int] = mapped_column(SmallInteger)
    cargo_type: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    weight_g: Mapped[int] = mapped_column(Integer)
    length_mm: Mapped[int] = mapped_column(Integer)
    width_mm: Mapped[int] = mapped_column(Integer)
    height_mm: Mapped[int] = mapped_column(Integer)


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
    # Первичная готовность дрона к заказу, подтверждённая техником парка
    tech_ready_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    tech_ready_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    offered_operator_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))   # предложен оператору
    offered_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    duty_technician_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))    # замещающий техник
    map_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("map_providers.id"))   # карта заказчика
    repeat_of: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"))      # повторный вылет заказа
    priority: Mapped[bool] = mapped_column(Boolean, default=False)
    consignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("consignees.id"))
    consignee: Mapped[Optional[Any]] = mapped_column(JSONB)              # грузополучатель на момент заказа
    priority_level: Mapped[str] = mapped_column(Text, default="normal")   # low | normal | high | highest
    coordinator_approved_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    customer_comment: Mapped[Optional[str]] = mapped_column(Text)
    comment_photo_key: Mapped[Optional[str]] = mapped_column(Text)
    comment_photo_type: Mapped[Optional[str]] = mapped_column(Text)
    asap: Mapped[bool] = mapped_column(Boolean, default=False)

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
    # Точка А — база парка (у заказов до 0007 пусто: маршрут А→Б)
    base_lat: Mapped[Optional[float]] = mapped_column(Float)
    base_lon: Mapped[Optional[float]] = mapped_column(Float)
    base_address: Mapped[Optional[str]] = mapped_column(Text)
    return_m: Mapped[Optional[int]] = mapped_column(Integer)    # возврат от получателя на базу
    stops: Mapped[Any] = mapped_column(JSONB, default=list)       # промежуточные точки между Б и В
    pickup_at_base: Mapped[bool] = mapped_column(Boolean, default=False)   # груз забирается на базе (А)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    order: Mapped[Order] = relationship(back_populates="route")


class RepeatRequest(Base):
    """Запрос заказчика на повторный вылет; согласует координатор заказа."""
    __tablename__ = "repeat_requests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="pending")
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    decision_comment: Mapped[Optional[str]] = mapped_column(Text)
    new_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


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
    coordinator_verdict: Mapped[Optional[str]] = mapped_column(Text)
    coordinator_comment: Mapped[Optional[str]] = mapped_column(Text)
    coordinator_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    coordinator_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
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


class ServiceZone(Base):
    """Зона обслуживания: прямоугольник, внутри которого работают парки назначенных техников."""
    __tablename__ = "service_zones"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    south: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class ServiceZoneTechnician(Base):
    __tablename__ = "service_zone_technicians"
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_zones.id"), primary_key=True)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class PlatformSetting(Base):
    """Настройка платформы, изменяемая администратором (например, зона обслуживания)."""
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class MapProvider(Base):
    """Онлайн-карта (плиточный сервис) с API-ключом; плитки отдаются через сервер платформы."""
    __tablename__ = "map_providers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    preset: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url_template: Mapped[str] = mapped_column(Text, default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    projection: Mapped[str] = mapped_column(Text, default="3857")
    min_zoom: Mapped[int] = mapped_column(SmallInteger, default=1)
    max_zoom: Mapped[int] = mapped_column(SmallInteger, default=19)
    attribution: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    last_error_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


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


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(Text, default="new")
    status_changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    status_changed_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    last_message_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class SupportParticipant(Base):
    __tablename__ = "support_participants"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")
    last_read_at: Mapped[Optional[dt.datetime]] = mapped_column(TS)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"))
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default="now()")


class SupportAttachment(Base):
    __tablename__ = "support_attachments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("support_messages.id"))
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, default="")
