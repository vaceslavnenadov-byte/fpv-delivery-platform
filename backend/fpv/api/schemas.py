"""Схемы входных данных. Все данные проверяются на сервере (типы, диапазоны, длины)."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PHONE_PATTERN = r"^\+?[0-9 ()-]{7,20}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    device_code: Optional[str] = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]{2,64}$")


class ProfileIn(StrictModel):
    display_name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = Field(default=None, pattern=PHONE_PATTERN)
    email: Optional[str] = Field(default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    nickname: Optional[str] = Field(default=None, max_length=60)
    location: Optional[str] = Field(default=None, max_length=200)


class OperatorApplyIn(StrictModel):
    license_number: str = Field(min_length=3, max_length=64)
    notes: str = Field(default="", max_length=1000)


class DroneIn(StrictModel):
    serial_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._/-]+$")
    registration_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9А-Яа-я._/-]+$")
    manufacturer: str = Field(min_length=2, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    adapter_kind: Literal["simulator", "vendor_sdk", "mavlink_readonly"] = "simulator"
    max_payload_g: int = Field(ge=50, le=25000)
    max_range_m: int = Field(ge=500, le=100000)
    fleet_id: Optional[uuid.UUID] = None
    drone_model_id: Optional[uuid.UUID] = None       # конфигурация из справочника «Типы БПЛА»
    link_type: Optional[str] = Field(default=None, max_length=100)
    construction: Optional[str] = Field(default=None, max_length=100)
    engine_type: Optional[str] = Field(default=None, max_length=100)


class ArchiveIn(StrictModel):
    reason: Literal["broken", "lost", "repair", "returned", "worn_out", "dismantled", "obsolete", "peripherals",
                    "other"]
    comment: str = Field(default="", max_length=500)


PRIORITY = Literal["low", "normal", "high", "highest"]


class ConsigneeIn(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="", max_length=120)
    recommended_cargo: Optional[str] = Field(default=None, max_length=40)
    location: str = Field(default="", max_length=500)
    priority: PRIORITY = "normal"
    comment: str = Field(default="", max_length=2000)


class ConsigneePatch(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: Optional[str] = Field(default=None, max_length=120)
    recommended_cargo: Optional[str] = Field(default=None, max_length=40)
    location: Optional[str] = Field(default=None, max_length=500)
    priority: Optional[PRIORITY] = None
    comment: Optional[str] = Field(default=None, max_length=2000)


class CargoTypeIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=120)
    max_weight_g: int = Field(ge=1, le=100000)


class CargoTypePatch(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=120)
    max_weight_g: Optional[int] = Field(default=None, ge=1, le=100000)


class DroneModelIn(StrictModel):
    model: str = Field(min_length=1, max_length=100)
    manufacturer: str = Field(min_length=2, max_length=100)
    link_type: str = Field(default="", max_length=100)
    construction: str = Field(default="", max_length=100)
    engine_type: str = Field(default="", max_length=100)
    max_range_m: int = Field(ge=500, le=100000)
    max_payload_g: int = Field(ge=50, le=25000)


class DroneModelPatch(StrictModel):
    model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(default=None, min_length=2, max_length=100)
    link_type: Optional[str] = Field(default=None, max_length=100)
    construction: Optional[str] = Field(default=None, max_length=100)
    engine_type: Optional[str] = Field(default=None, max_length=100)
    max_range_m: Optional[int] = Field(default=None, ge=500, le=100000)
    max_payload_g: Optional[int] = Field(default=None, ge=50, le=25000)


class ServiceZoneIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    south: float = Field(ge=-85, le=85)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)
    active: Optional[bool] = None
    confirm: bool = False


class ServiceZonePatch(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    south: Optional[float] = Field(default=None, ge=-85, le=85)
    west: Optional[float] = Field(default=None, ge=-180, le=180)
    north: Optional[float] = Field(default=None, ge=-85, le=85)
    east: Optional[float] = Field(default=None, ge=-180, le=180)
    active: Optional[bool] = None
    confirm: bool = False          # сохранить, даже если базы каких-то парков окажутся вне зоны


class MapProviderIn(StrictModel):
    preset: Optional[str] = Field(default=None, pattern=r"^[a-z_]{2,40}$")
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    url_template: Optional[str] = Field(default=None, max_length=1000)
    api_key: Optional[str] = Field(default=None, max_length=2000)
    projection: Optional[Literal["3857", "3395"]] = None
    min_zoom: Optional[int] = Field(default=None, ge=0, le=22)
    max_zoom: Optional[int] = Field(default=None, ge=0, le=22)
    attribution: Optional[str] = Field(default=None, max_length=300)
    active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)


class MapUpdateIn(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    active: Optional[bool] = None
    north: Optional[float] = Field(default=None, ge=-85, le=85)
    south: Optional[float] = Field(default=None, ge=-85, le=85)
    west: Optional[float] = Field(default=None, ge=-180, le=180)
    east: Optional[float] = Field(default=None, ge=-180, le=180)


class DroneDecisionIn(StrictModel):
    action: Literal["approve", "block", "unblock", "retire"]
    reason: str = Field(default="", max_length=500)


class InspectionIn(StrictModel):
    result: Literal["pass", "warn", "fail"]
    set_status: Literal["serviceable", "requires_check", "grounded"]
    battery_pct: Optional[int] = Field(default=None, ge=0, le=100)
    error_codes: List[str] = Field(default_factory=list, max_length=20)
    comment: str = Field(default="", max_length=2000)


class MaintenanceIn(StrictModel):
    kind: Literal["inspection", "repair", "component_replacement", "firmware_update"]
    description: str = Field(min_length=3, max_length=2000)
    components: List[Dict[str, str]] = Field(default_factory=list, max_length=50)
    set_status: Optional[Literal["requires_check", "grounded"]] = None


class PointIn(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address: str = Field(min_length=3, max_length=300)


class StopIn(PointIn):
    note: str = Field(default="", max_length=200)


MAX_STOPS = 5


CARGO_CODE = r"^[a-z0-9_]+$"
MAX_CARGO_ITEMS = 10


class CargoItemIn(StrictModel):
    cargo_type: str = Field(min_length=2, max_length=40, pattern=CARGO_CODE)
    description: str = Field(min_length=3, max_length=1000)
    weight_g: int = Field(ge=1, le=100000)
    length_mm: int = Field(ge=1, le=5000)
    width_mm: int = Field(ge=1, le=5000)
    height_mm: int = Field(ge=1, le=5000)


class OrderIn(StrictModel):
    drone_id: uuid.UUID
    # Грузы заказа (один дрон). Поля cargo_* — прежний способ указать один груз.
    cargo_items: List[CargoItemIn] = Field(default_factory=list, max_length=MAX_CARGO_ITEMS)
    cargo_type: Optional[str] = Field(default=None, min_length=2, max_length=40, pattern=CARGO_CODE)
    cargo_description: Optional[str] = Field(default=None, min_length=3, max_length=1000)
    cargo_weight_g: Optional[int] = Field(default=None, ge=1, le=100000)
    cargo_length_mm: Optional[int] = Field(default=None, ge=1, le=5000)
    cargo_width_mm: Optional[int] = Field(default=None, ge=1, le=5000)
    cargo_height_mm: Optional[int] = Field(default=None, ge=1, le=5000)
    # Маршрут заказчика: точки Б, В, … по порядку (груз забирается на базе парка — точка А);
    # последняя точка — грузополучатель. Прежний способ — origin (забор груза) и destination.
    points: List[StopIn] = Field(default_factory=list, max_length=MAX_STOPS + 1)
    origin: Optional[PointIn] = None
    destination: Optional[PointIn] = None
    stops: List[StopIn] = Field(default_factory=list, max_length=MAX_STOPS)
    map_provider_id: Optional[uuid.UUID] = None       # онлайн-карта, выбранная заказчиком
    consignee_id: Optional[uuid.UUID] = None          # из справочника «Типы грузополучателей»
    recipient_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    recipient_phone: Optional[str] = Field(default=None, pattern=PHONE_PATTERN)
    window_start: Optional[dt.datetime] = None        # без окна — доставка как можно скорее
    window_end: Optional[dt.datetime] = None
    customer_comment: str = Field(default="", max_length=2000)

    @field_validator("window_start", "window_end")
    @classmethod
    def _tz(cls, v: Optional[dt.datetime]) -> Optional[dt.datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        return v

    @model_validator(mode="after")
    def _window(self) -> "OrderIn":
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Укажите и начало, и конец временного окна (или не указывайте его)")
        if self.window_start is not None and self.window_end <= self.window_start:
            raise ValueError("Конец временного окна должен быть позже начала")
        single = (self.cargo_type, self.cargo_description, self.cargo_weight_g, self.cargo_length_mm,
                  self.cargo_width_mm, self.cargo_height_mm)
        if not self.cargo_items and any(v is None for v in single):
            raise ValueError("Добавьте хотя бы один груз")
        if not self.points and (self.origin is None or self.destination is None):
            raise ValueError("Добавьте хотя бы одну точку маршрута")
        if self.consignee_id is None and not self.recipient_name:
            raise ValueError("Выберите грузополучателя")
        return self


class LicenseDecisionIn(StrictModel):
    status: Literal["approved", "fix", "rejected"]
    comment: str = Field(default="", max_length=1000)


class CoordinatorDecisionIn(StrictModel):
    approve: bool
    comment: str = Field(default="", max_length=1000)


class VersionIn(StrictModel):
    expected_version: Optional[int] = Field(default=None, ge=1)


class ReasonIn(VersionIn):
    reason: str = Field(default="", max_length=1000)


class AbortIn(StrictModel):
    reason: str = Field(min_length=3, max_length=1000)


class ReportIn(StrictModel):
    summary: str = Field(min_length=3, max_length=4000)
    handed_over_to: str = Field(default="", max_length=200)
    remarks: str = Field(default="", max_length=4000)


class ReceiptIn(StrictModel):
    received: bool
    comment: str = Field(default="", max_length=2000)


class DisputeIn(StrictModel):
    description: str = Field(min_length=5, max_length=4000)


class ReviewIn(VersionIn):
    approve: bool
    comment: str = Field(default="", max_length=2000)
    video_verified: Optional[bool] = None


class NoteIn(StrictModel):
    kind: Literal["remark", "message_to_operator", "incident", "deviation"]
    severity: Literal["info", "warning", "critical"] = "info"
    body: str = Field(min_length=1, max_length=4000)


class ChatIn(StrictModel):
    body: str = Field(min_length=1, max_length=2000)
    attach_frame: bool = False


class AssignCoordinatorIn(StrictModel):
    coordinator_id: uuid.UUID


class SupportTicketIn(StrictModel):
    reason: Literal["order", "account", "group", "service", "suggestion", "other"]
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=5000)
    order_id: Optional[uuid.UUID] = None


class SupportMessageIn(StrictModel):
    body: str = Field(min_length=1, max_length=5000)


class SupportStatusIn(StrictModel):
    status: Literal["in_progress", "rejected", "done"]


class SupportParticipantIn(StrictModel):
    user_id: uuid.UUID


class RepeatRequestIn(StrictModel):
    comment: str = Field(default="", max_length=2000)


class RepeatDecisionIn(StrictModel):
    approve: bool
    comment: str = Field(default="", max_length=2000)


class DisputeReviewIn(StrictModel):
    upheld: bool
    comment: str = Field(min_length=5, max_length=4000)


class ResolveComplaintIn(StrictModel):
    upheld: bool
    resolution: str = Field(min_length=5, max_length=4000)
    points_adjustment: Optional[int] = Field(default=None, ge=-100, le=100)


class ScoringRulesIn(StrictModel):
    rules: Dict[str, Any]
    comment: str = Field(default="", max_length=500)


class UserStatusIn(StrictModel):
    status: Literal["active", "disabled", "deleted"]
    reason: str = Field(min_length=3, max_length=500)


ROLE_CODE = Literal["customer", "operator", "coordinator", "technician", "recruiter", "admin"]


class LocalUserIn(StrictModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=200)
    last_name: str = Field(min_length=1, max_length=80)
    first_name: str = Field(min_length=1, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    position_title: Optional[str] = Field(default=None, max_length=160)
    profile_notes: Optional[str] = Field(default=None, max_length=2000)
    email: Optional[str] = Field(default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: Optional[str] = Field(default=None, pattern=PHONE_PATTERN)
    org_unit_id: Optional[uuid.UUID] = None
    roles: List[ROLE_CODE] = Field(default_factory=list, max_length=6)
    password_confirm: Optional[str] = Field(default=None, max_length=200)   # повтор пароля

    @model_validator(mode="after")
    def _confirm(self) -> "LocalUserIn":
        if self.password_confirm is not None and self.password != self.password_confirm:
            raise ValueError("Пароли не совпадают")
        return self


class PersonProfileIn(StrictModel):
    last_name: Optional[str] = Field(default=None, max_length=80)
    first_name: Optional[str] = Field(default=None, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    position_title: Optional[str] = Field(default=None, max_length=160)
    profile_notes: Optional[str] = Field(default=None, max_length=2000)
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=40)
    nickname: Optional[str] = Field(default=None, max_length=60)
    location: Optional[str] = Field(default=None, max_length=200)
    org_unit_id: Optional[uuid.UUID] = None


class CredentialsIn(StrictModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    password: Optional[str] = Field(default=None, min_length=10, max_length=200)
    password_confirm: Optional[str] = Field(default=None, max_length=200)   # повтор нового пароля

    @model_validator(mode="after")
    def _confirm(self) -> "CredentialsIn":
        if self.password is not None and self.password_confirm is not None and self.password != self.password_confirm:
            raise ValueError("Пароли не совпадают")
        return self


class AccountStatusIn(StrictModel):
    status: Literal["active", "suspended", "banned"]
    reason: str = Field(default="", max_length=500)


class OrgUnitIn(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    parent_id: Optional[uuid.UUID] = None


class OrgUnitPatch(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    parent_id: Optional[uuid.UUID] = None
    move: bool = False             # true — применить parent_id (в том числе null — сделать корневой)


class RecruitUserIn(StrictModel):
    username: str = Field(min_length=2, max_length=120)
    roles: List[Literal["customer", "operator", "coordinator", "technician", "recruiter", "admin"]] = Field(
        default_factory=list, max_length=6)


class ProfileReviewIn(StrictModel):
    approve: bool
    notes: str = Field(default="", max_length=1000)


class RoleGrantIn(StrictModel):
    role: Literal["customer", "operator", "coordinator", "technician", "recruiter", "admin"]
    grant: bool


class DomainGroupIn(StrictModel):
    dn: str = Field(min_length=3, max_length=500)
    name: str = Field(min_length=2, max_length=128)
    role: Optional[Literal["customer", "operator", "coordinator", "technician", "recruiter", "admin"]] = None
    description: str = Field(default="", max_length=500)


class OperatorDecisionIn(StrictModel):
    approve: bool
    notes: str = Field(default="", max_length=1000)


class DeviceIn(StrictModel):
    code: str = Field(pattern=r"^[A-Za-z0-9._-]{2,64}$")
    kind: Literal["workstation", "server", "ground_station", "mobile", "other"]
    hostname: Optional[str] = Field(default=None, max_length=253)
    description: str = Field(default="", max_length=500)
    domain_joined: bool = False


class DevicePolicyIn(StrictModel):
    role: Optional[Literal["customer", "operator", "coordinator", "technician", "recruiter", "admin"]] = None
    domain_group: Optional[str] = Field(default=None, max_length=128)
    access_level: Literal["use", "admin"] = "use"

    @model_validator(mode="after")
    def _one(self) -> "DevicePolicyIn":
        if (self.role is None) == (self.domain_group is None):
            raise ValueError("Укажите либо роль, либо доменную группу")
        return self


# --- контур сервисного модуля дрона ---

class TelemetryPointIn(StrictModel):
    seq: int = Field(ge=0)
    recorded_at: dt.datetime
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    alt_m: Optional[float] = Field(default=None, ge=-100, le=1000)
    ground_speed_mps: Optional[float] = Field(default=None, ge=0, le=100)
    heading_deg: Optional[float] = Field(default=None, ge=0, le=360)
    battery_pct: Optional[int] = Field(default=None, ge=0, le=100)
    link_quality: Optional[int] = Field(default=None, ge=0, le=100)
    gnss_fix: Optional[bool] = None
    flight_phase: Optional[Literal["idle", "preflight", "takeoff", "cruise", "approach", "pickup", "delivery",
                                   "return", "landed"]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _extra_small(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        # Дополнительные показатели — плоский набор скалярных значений ограниченного размера.
        if len(v) > 32 or any(len(k) > 40 or not (v[k] is None or isinstance(v[k], (int, float, str, bool)))
                              or (isinstance(v[k], str) and len(v[k]) > 64) for k in v):
            raise ValueError("extra: не более 32 скалярных полей")
        return v

    @field_validator("recorded_at")
    @classmethod
    def _tz(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        return v


class TelemetryBatchIn(StrictModel):
    records: List[TelemetryPointIn] = Field(min_length=1, max_length=500)


class FleetIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    base: "PointIn"


class FleetUpdateIn(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    base: Optional["PointIn"] = None
    active: Optional[bool] = None


class PublishIn(StrictModel):
    published: bool


class FleetAssignIn(StrictModel):
    fleet_id: uuid.UUID


class RevokeIn(StrictModel):
    reason: str = Field(min_length=3, max_length=1000)


class VideoSourceItem(StrictModel):
    id: str = Field(max_length=110)
    kind: Literal["usb", "rtsp", "file", "test"]
    label: str = Field(default="", max_length=200)


class VideoSourcesIn(StrictModel):
    sources: List[VideoSourceItem] = Field(max_length=64)
    active: Optional[str] = Field(default=None, max_length=110)


class VideoSourceSelectIn(StrictModel):
    source_id: str = Field(max_length=110)


class DeviceEventIn(StrictModel):
    kind: Literal["delivery_completed", "cargo_picked_up", "error", "self_check"]
    data: Dict[str, Any] = Field(default_factory=dict)
