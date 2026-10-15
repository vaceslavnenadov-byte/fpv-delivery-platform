"""Интерфейс адаптера оборудования.

Адаптер изолирует платформу от конкретной модели дрона: бизнес-логика
работает только с этим интерфейсом. Для реального оборудования адаптер
реализуется поверх документированного SDK производителя или стандартного
протокола в режиме ЧТЕНИЯ (телеметрия, диагностика, видео).

Интерфейс сознательно НЕ содержит методов управления полётом (взлёт,
навигация, изменение маршрута). Интеграция команд — отдельный этап с
самостоятельной проверкой безопасности и соответствия законодательству
(см. docs/hardware-integration-plan.md).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MissionInfo:
    """Справочная информация о задании, полученная с сервера (не команда)."""
    active: bool
    order_id: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[Dict[str, float]] = None
    destination: Optional[Dict[str, float]] = None
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    max_altitude_m: Optional[float] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "MissionInfo":
        if not data.get("active"):
            return cls(active=False)
        route = data.get("route") or {}
        return cls(active=True, order_id=data.get("order_id"), status=data.get("status"),
                   origin=route.get("origin"), destination=route.get("destination"),
                   checkpoints=route.get("checkpoints") or [], max_altitude_m=route.get("max_altitude_m"))


class DroneAdapter(abc.ABC):
    #: Возможности адаптера. flight_control всегда False в первой версии.
    capabilities: Dict[str, bool] = {"telemetry": True, "video": True, "diagnostics": True, "flight_control": False}

    @abc.abstractmethod
    def connect(self) -> None:
        """Подключение к бортовому оборудованию / наземной станции."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        ...

    @abc.abstractmethod
    def read_telemetry(self) -> Dict[str, Any]:
        """Текущее состояние: lat, lon, alt_m, ground_speed_mps, heading_deg, battery_pct,
        link_quality, gnss_fix, flight_phase."""

    @abc.abstractmethod
    def self_check(self) -> Dict[str, Any]:
        """Результат самодиагностики: result (pass|warn|fail), error_codes, battery_pct, firmware_version."""

    @abc.abstractmethod
    def read_video_segment(self) -> Optional[bytes]:
        """Очередной сегмент видеопотока (MPEG-TS, ~1 с) или None, если поток недоступен."""

    def pending_events(self) -> List[Dict[str, Any]]:
        """События оборудования (ошибки, завершение доставки по датчику сброса груза и т.п.)."""
        return []

    def on_mission_update(self, mission: MissionInfo) -> None:
        """Уведомление о статусе задания (только информация, без управления)."""

    def link_available(self) -> bool:
        """Есть ли канал передачи данных до сервера (для имитации потери связи)."""
        return True
