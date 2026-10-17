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


class OrderIn(StrictModel):
    drone_id: uuid.UUID
    cargo_type: str = Field(min_length=2, max_length=40, pattern=r"^[a-z_]+$")
    cargo_description: str = Field(min_length=3, max_length=1000)
    cargo_weight_g: int = Field(ge=1, le=100000)
    cargo_length_mm: int = Field(ge=1, le=5000)
    cargo_width_mm: int = Field(ge=1, le=5000)
    cargo_height_mm: int = Field(ge=1, le=5000)
    origin: PointIn
    destination: PointIn
    stops: List[StopIn] = Field(default_factory=list, max_length=MAX_STOPS)
    map_provider_id: Optional[uuid.UUID] = None       # онлайн-карта, выбранная заказчиком
    recipient_name: str = Field(min_length=2, max_length=120)
    recipient_phone: str = Field(pattern=PHONE_PATTERN)
    window_start: dt.datetime
    window_end: dt.datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def _tz(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        return v

    @model_validator(mode="after")
    def _window(self) -> "OrderIn":
        if self.window_end <= self.window_start:
            raise ValueError("Конец временного окна должен быть позже начала")
        return self


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
