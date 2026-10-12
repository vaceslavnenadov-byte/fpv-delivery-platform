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


class AssignCoordinatorIn(StrictModel):
    coordinator_id: uuid.UUID


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


class RoleGrantIn(StrictModel):
    role: Literal["customer", "operator", "coordinator", "technician", "admin"]
    grant: bool


class DomainGroupIn(StrictModel):
    dn: str = Field(min_length=3, max_length=500)
    name: str = Field(min_length=2, max_length=128)
    role: Optional[Literal["customer", "operator", "coordinator", "technician", "admin"]] = None
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
    role: Optional[Literal["customer", "operator", "coordinator", "technician", "admin"]] = None
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
    flight_phase: Optional[Literal["idle", "preflight", "takeoff", "cruise", "approach", "delivery",
                                   "return", "landed"]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("recorded_at")
    @classmethod
    def _tz(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        return v


class TelemetryBatchIn(StrictModel):
    records: List[TelemetryPointIn] = Field(min_length=1, max_length=500)


class DeviceEventIn(StrictModel):
    kind: Literal["delivery_completed", "error", "self_check"]
    data: Dict[str, Any] = Field(default_factory=dict)
