"""Симулятор дрона и сервисный модуль против реального API (без сети: транспорт через TestClient)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "drone-agent"))

from fpv_agent.adapters import DroneAdapter, SimulatedDrone, SimulatorScenario  # noqa: E402
from fpv_agent.agent import LinkDown, ServiceModule  # noqa: E402


class InProcessClient:
    """Транспорт агента поверх TestClient; умеет имитировать обрыв связи."""

    def __init__(self, app, key):
        from fastapi.testclient import TestClient
        self.c = TestClient(app)
        self.key = key
        self.down = False
        self.calls = []

    def request(self, method, path, body=None, *, raw=None, headers=None):
        if self.down:
            raise LinkDown("сеть недоступна")
        h = {"Authorization": f"Drone {self.key}", **(headers or {})}
        self.calls.append(path)
        if raw is not None:
            r = self.c.request(method, path, content=raw, headers={**h, "Content-Type": "video/mp2t"})
        else:
            r = self.c.request(method, path, json=body, headers=h)
        data = r.json()
        if r.status_code >= 400:
            if r.status_code in (401, 403):
                raise PermissionError(data)
            data["_status"] = r.status_code
        return data


def test_adapter_interface_has_no_flight_commands():
    names = {n for n in dir(DroneAdapter) if not n.startswith("_")}
    forbidden = {"takeoff", "land", "goto", "arm", "set_waypoint", "send_command", "set_mode"}
    assert not (names & forbidden)
    assert DroneAdapter.capabilities["flight_control"] is False


def test_agent_full_mission_with_simulator(world, tmp_path):
    o = world.ready_order()
    client = InProcessClient(world.app, world.drone_key)
    fake_time = {"t": 0.0}

    def tick():
        fake_time["t"] += 10.0       # каждый такт агента = 10 с модельного времени
        return fake_time["t"]

    sim = SimulatedDrone(SimulatorScenario.preset("fast"), clock=tick)
    sim.read_video_segment = lambda: (b"SIMSEG" * 200) if sim.mission_started else None   # быстрее ffmpeg
    agent = ServiceModule(sim, client, tmp_path, auto_complete=True)
    agent.adapter.connect()
    agent.step()
    assert agent.mission.status == "ready"
    world.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
    agent._last_mission = 0
    for _ in range(25):
        agent._last_mission = 0
        agent.step()
        if world.status(o["id"]) != "in_progress":
            break
    assert world.status(o["id"]) == "awaiting_delivery_confirmation"
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["video"]["segment_count"] >= 1
    tel = world.operator.get(f"/api/v1/orders/{o['id']}/telemetry").json()["items"]
    assert len(tel) >= 3 and tel[-1]["flight_phase"] == "delivery"


def test_agent_buffers_telemetry_during_link_loss(world, tmp_path):
    client = InProcessClient(world.app, world.drone_key)
    agent = ServiceModule(SimulatedDrone(), client, tmp_path)
    agent.step()
    client.down = True
    for _ in range(7):
        agent.step()
    assert agent.online is False and len(agent.queue) == 7
    client.down = False
    agent.step()
    assert agent.online is True and len(agent.queue) == 0
    from sqlalchemy import text
    from fpv import db as dbmod
    with dbmod.get_engine().connect() as c:
        stored = c.execute(text("SELECT count(*) FROM telemetry_records WHERE seq > 1")).scalar()
    assert stored >= 8


def test_agent_sequence_survives_restart(world, tmp_path):
    client = InProcessClient(world.app, world.drone_key)
    a1 = ServiceModule(SimulatedDrone(), client, tmp_path)
    a1.seq = 1000
    for _ in range(3):
        a1.step()
    a2 = ServiceModule(SimulatedDrone(), client, tmp_path)
    assert a2.seq == 1003           # номера не переиспользуются после перезапуска


def test_faulty_simulator_reports_self_check(world, tmp_path):
    client = InProcessClient(world.app, world.drone_key)
    agent = ServiceModule(SimulatedDrone(SimulatorScenario.preset("faulty")), client, tmp_path)
    agent.step()
    drone = world.technician.get(f"/api/v1/drones/{world.drone_id}").json()
    assert drone["health_status"] == "requires_check"
    assert "SIM_IMU_CALIBRATION" in drone["health_checks"][0]["error_codes"]


def test_agent_rejects_plain_http_to_remote_server():
    from fpv_agent.agent import ServerClient
    with pytest.raises(ValueError):
        ServerClient("http://fpv.example.org", "k")


# --- камера дрона (--video-device) ---

from fpv_agent.camera import CameraSource  # noqa: E402
from fpv_agent.__main__ import main as agent_main  # noqa: E402


class _TestPatternCamera(CameraSource):
    """Та же цепочка сегментирования, но вход — тестовый источник вместо /dev/videoN."""

    def command(self, out_dir):
        cmd = super().command(out_dir)
        i = cmd.index("-f")
        return cmd[:i] + ["-re", "-f", "lavfi", "-i", f"testsrc=size={self.size}:rate={self.fps}"] + cmd[cmd.index(self.device) + 1:]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="нет ffmpeg")
def test_camera_segments_used_as_drone_video(tmp_path):
    import time
    dev = tmp_path / "video0"
    dev.touch()                                   # устройство «существует»
    cam = _TestPatternCamera(str(dev), "320x240", 10)
    drone = SimulatedDrone(SimulatorScenario(), camera=cam)
    assert drone.read_video_segment() is None     # вне задания камера не открывается
    assert not cam.running
    drone.mission_started = drone.clock()
    got = []
    deadline = time.monotonic() + 15
    while len(got) < 2 and time.monotonic() < deadline:
        seg = drone.read_video_segment()
        if seg:
            got.append(seg)
        time.sleep(0.2)
    assert cam.running and len(got) >= 2
    assert all(s[0] == 0x47 for s in got)         # MPEG-TS (sync byte), как у видео дрона
    assert drone.read_telemetry()["extra"]["video_resolution"] == "320x240"
    drone.disconnect()
    assert not cam.running


def test_camera_missing_device_gives_no_video(tmp_path):
    cam = CameraSource(str(tmp_path / "nope"))
    assert cam.read_segment() is None and not cam.running


@pytest.mark.parametrize("args", [["--video-device", "/dev/sda"], ["--video-device", "/dev/video0;rm"],
                                  ["--video-device", "/dev/video0", "--video-size", "big"],
                                  ["--video-device", "/dev/video0", "--video-fps", "0"]])
def test_agent_rejects_bad_video_args(args, monkeypatch):
    monkeypatch.setenv("FPV_DRONE_KEY", "x")
    assert agent_main(["--server", "http://127.0.0.1:9", *args]) == 2
