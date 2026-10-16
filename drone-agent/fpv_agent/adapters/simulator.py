"""Симулятор дрона для тестирования без оборудования.

Имитирует: подключение, готовность, телеметрию, качество связи, полёт по
маршруту задания (как модель, а не управление), видеопоток, ошибки, потерю
и восстановление соединения. Через симулятор невозможно управлять реальным
оборудованием. Единственные внешние источники, которые он открывает, — видео (USB-камера, RTSP-поток,
тестовый ролик; только чтение, см. fpv_agent/video.py): выбранный оператором источник
идёт в платформу как видео дрона.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..video import VideoManager
from .base import DroneAdapter, MissionInfo

HOME = {"lat": 55.7500, "lon": 37.6000}
GROUND_MSL_M = 145.0          # высота поверхности над уровнем моря в районе имитации


@dataclass
class SimulatorScenario:
    """Сценарий имитации. Время — секунды от начала выполнения задания."""
    name: str = "normal"
    cruise_speed_mps: float = 12.0
    battery_drain_per_s: float = 0.05
    link_loss_at: Optional[float] = None
    link_loss_duration: float = 0.0
    critical_error_at: Optional[float] = None
    video_enabled: bool = True
    self_check_fail: bool = False

    @classmethod
    def preset(cls, name: str) -> "SimulatorScenario":
        presets = {
            "normal": cls(),
            "fast": cls(name="fast", cruise_speed_mps=60.0),
            "link-loss": cls(name="link-loss", link_loss_at=8.0, link_loss_duration=40.0),
            "link-blip": cls(name="link-blip", link_loss_at=8.0, link_loss_duration=12.0),
            "critical-error": cls(name="critical-error", critical_error_at=10.0),
            "no-video": cls(name="no-video", video_enabled=False),
            "faulty": cls(name="faulty", self_check_fail=True),
        }
        if name not in presets:
            raise ValueError(f"Неизвестный сценарий: {name}. Доступны: {', '.join(presets)}")
        return presets[name]


class SimulatedDrone(DroneAdapter):
    def __init__(self, scenario: Optional[SimulatorScenario] = None, serial: str = "SIM-0001",
                 rng: Optional[random.Random] = None, clock=time.monotonic,
                 video: Optional[VideoManager] = None) -> None:
        self.scenario = scenario or SimulatorScenario()
        self.serial = serial
        self.rng = rng or random.Random(42)
        self.clock = clock          # подменяется в тестах для ускоренной имитации
        self.connected = False
        self.battery = 98.0
        self.position = dict(HOME)
        self.alt = 0.0
        self.phase = "idle"
        self.mission: Optional[MissionInfo] = None
        self.mission_started: Optional[float] = None
        self._last_tick = self.clock()
        self._events: List[Dict[str, Any]] = []
        self._error_sent = False
        self._ffmpeg = shutil.which("ffmpeg")
        self._video_seq = 0
        self.video = video          # источники видео; без менеджера — тестовая таблица ffmpeg
        self.heading = 0.0
        self.cruise_alt = 80.0
        self._wp_index = 0
        self._vspeed = 0.0

    # --- подключение ---
    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False
        if self.video:
            self.video.stop()

    def _elapsed(self) -> float:
        return self.clock() - self.mission_started if self.mission_started else 0.0

    def link_available(self) -> bool:
        s = self.scenario
        if s.link_loss_at is None or self.mission_started is None:
            return True
        t = self._elapsed()
        return not (s.link_loss_at <= t < s.link_loss_at + s.link_loss_duration)

    # --- модель полёта (имитация, не управление) ---
    def on_mission_update(self, mission: MissionInfo) -> None:
        self.mission = mission
        if self.video:
            self.video.select(mission.video_source)
        if mission.active and mission.status == "in_progress" and self.mission_started is None:
            self.mission_started = self.clock()
            if mission.origin:
                self.position = {"lat": mission.origin["lat"], "lon": mission.origin["lon"]}
            self.cruise_alt = float(min(80.0, mission.max_altitude_m or 80.0))
            self._wp_index = 0
            self.phase = "takeoff"
        elif mission.active and mission.status == "ready":
            self.phase = "preflight"
        elif not mission.active and self.mission_started is not None:
            self.mission_started = None
            self.phase = "landed"
            self.alt = 0.0
            if self.video:
                self.video.stop()       # источник открыт только во время задания

    def _waypoints(self) -> List[Dict[str, float]]:
        m = self.mission
        if not m or not m.destination:
            return []
        cps = [{"lat": c["lat"], "lon": c["lon"]} for c in (m.checkpoints or []) if "lat" in c and "lon" in c]
        return cps + [{"lat": m.destination["lat"], "lon": m.destination["lon"]}]

    @staticmethod
    def _bearing(a: Dict[str, float], b: Dict[str, float]) -> float:
        dlon = math.radians(b["lon"] - a["lon"])
        la, lb = math.radians(a["lat"]), math.radians(b["lat"])
        y = math.sin(dlon) * math.cos(lb)
        x = math.cos(la) * math.sin(lb) - math.sin(la) * math.cos(lb) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def _advance(self) -> None:
        now = self.clock()
        dt = now - self._last_tick
        self._last_tick = now
        self._vspeed = 0.0
        if self.mission_started is None or not self.mission or not self.mission.destination:
            return
        self.battery = max(0.0, self.battery - self.scenario.battery_drain_per_s * dt)
        wps = self._waypoints()
        step = self.scenario.cruise_speed_mps * dt
        alt_before = self.alt
        # Перемещение по контрольным точкам маршрута к точке Б (модель для отображения, не управление).
        while self._wp_index < len(wps) and step > 0:
            target = wps[self._wp_index]
            dlat = target["lat"] - self.position["lat"]
            dlon = target["lon"] - self.position["lon"]
            dist_m = math.hypot(dlat * 111_320, dlon * 111_320 * math.cos(math.radians(self.position["lat"])))
            if dist_m > 0.5:
                self.heading = self._bearing(self.position, target)
            if dist_m <= max(step, 1.0):
                self.position = {"lat": target["lat"], "lon": target["lon"]}
                step -= dist_m
                self._wp_index += 1
            else:
                frac = step / dist_m
                self.position["lat"] += dlat * frac
                self.position["lon"] += dlon * frac
                step = 0
        if self._wp_index >= len(wps):
            if self.phase != "delivery":
                self.phase = "delivery"
            self.alt = max(0.0, self.alt - 5 * dt)
        else:
            self.alt = min(self.cruise_alt, self.alt + 4 * dt)
            self.phase = "cruise" if self.alt >= 30 else "takeoff"
        self._vspeed = (self.alt - alt_before) / dt if dt > 0 else 0.0
        s = self.scenario
        if s.critical_error_at is not None and not self._error_sent and self._elapsed() >= s.critical_error_at:
            self._error_sent = True
            self._events.append({"kind": "error", "data": {"code": "SIM_MOTOR_OVERHEAT", "severity": "critical",
                                                            "message": "Имитация: перегрев двигателя"}})

    def read_telemetry(self) -> Dict[str, Any]:
        self._advance()
        link = self.rng.randint(80, 100) if self.link_available() else 0
        cruising = self.phase == "cruise"
        speed = self.scenario.cruise_speed_mps * (1 + self.rng.uniform(-0.04, 0.04)) if cruising else 0.0
        total = len(self._waypoints())
        return {"lat": round(self.position["lat"], 6), "lon": round(self.position["lon"], 6),
                "alt_m": round(self.alt, 1), "ground_speed_mps": round(speed, 1),
                "heading_deg": round(self.heading, 1), "battery_pct": int(self.battery), "link_quality": link,
                "gnss_fix": True, "flight_phase": self.phase,
                "extra": {"satellites": self.rng.randint(14, 18),
                          "voltage_v": round(21.0 + 4.2 * self.battery / 100, 2),
                          "alt_msl_m": round(self.alt + GROUND_MSL_M, 1),
                          "vspeed_mps": round(self._vspeed, 1),
                          "wind_mps": round(3.5 + self.rng.uniform(-0.8, 0.8), 1), "wind_dir_deg": 225,
                          "pitch_deg": round(-6.0 + self.rng.uniform(-1.5, 1.5), 1) if cruising else 0.0,
                          "roll_deg": round(self.rng.uniform(-3, 3), 1) if cruising else 0.0,
                          "flight_mode": "auto" if self.mission_started else "manual",
                          "waypoint_index": min(self._wp_index + 1, total) if total else 0,
                          "waypoint_total": total,
                          "video_resolution": self.video.size if self.video else "320x240",
                          "video_fps": self.video.fps if self.video else 10}}

    def self_check(self) -> Dict[str, Any]:
        if self.scenario.self_check_fail:
            return {"result": "fail", "error_codes": ["SIM_IMU_CALIBRATION"], "battery_pct": int(self.battery),
                    "firmware_version": "sim-1.0.0"}
        return {"result": "pass", "error_codes": [], "battery_pct": int(self.battery),
                "firmware_version": "sim-1.0.0", "sensors": {"imu": "ok", "gnss": "ok", "camera": "ok"}}

    def read_video_segment(self) -> Optional[bytes]:
        if not self.scenario.video_enabled or self.mission_started is None:
            return None
        self._video_seq += 1
        if self.video and self.video.ffmpeg:
            return self.video.read_segment()
        if self._ffmpeg:
            return self._ffmpeg_segment()
        # Без ffmpeg — синтетические байты в контейнере-заглушке (для проверки буфера и хранения).
        return b"SIMTS" + self._video_seq.to_bytes(4, "big") + os.urandom(2048)

    def _ffmpeg_segment(self) -> Optional[bytes]:
        clock_txt = time.strftime("%H:%M:%S").replace(":", "\\:")   # экранирование для drawtext
        text = "{} seg {} {}".format(self.serial, self._video_seq, clock_txt)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "seg.ts"
            cmd = [self._ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                   "-t", "1", "-vf", f"drawtext=text='{text}':x=10:y=10:fontcolor=white:box=1:boxcolor=black",
                   "-c:v", "mpeg2video", "-f", "mpegts", str(out)]
            try:
                subprocess.run(cmd, check=True, timeout=10, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (subprocess.SubprocessError, OSError):
                cmd = [c for c in cmd if not c.startswith("drawtext")]
                idx = cmd.index("-vf")
                del cmd[idx:idx + 2]
                try:
                    subprocess.run(cmd, check=True, timeout=10, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (subprocess.SubprocessError, OSError):
                    return None
            return out.read_bytes()

    def video_inventory(self, force: bool = False) -> Optional[Dict[str, Any]]:
        if not self.video:
            return None
        changed = self.video.rescan(force=force)
        active = self.video.active_id
        if force or changed or active != getattr(self, "_reported_active", None):
            self._reported_active = active
            return self.video.inventory()
        return None

    def pending_events(self) -> List[Dict[str, Any]]:
        events, self._events = self._events, []
        return events

    @property
    def arrived(self) -> bool:
        return self.phase == "delivery"
