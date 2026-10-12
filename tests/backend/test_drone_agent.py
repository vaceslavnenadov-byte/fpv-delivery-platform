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
