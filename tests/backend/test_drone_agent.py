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


def test_simulator_flies_base_pickup_recipient(world, tmp_path):
    """А (база) → Б (забор груза, остановка) → В (получатель); видео идёт и при проверке техником без заказа."""
    o = world.placed_order()
    client = InProcessClient(world.app, world.drone_key)
    fake_time = {"t": 0.0}

    def tick():
        fake_time["t"] += 10.0
        return fake_time["t"]

    sim = SimulatedDrone(SimulatorScenario.preset("fast"), clock=tick)
    sim.read_video_segment = lambda: b"SIMSEG" * 200 if (sim.mission_started or sim.mission.video_check) else None
    agent = ServiceModule(sim, client, tmp_path, auto_complete=True)
    agent.adapter.connect()
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    world.technician.post(f"/api/v1/drones/{world.drone_id}/video-check")
    agent._last_mission = 0
    agent.step()                                  # справка: проверка видео → сегмент в буфер дрона
    assert agent.mission.video_check is True
    assert "/drone-link/v1/video/segments" in client.calls
    r = world.technician.post(f"/api/v1/orders/{o['id']}/tech-ready")
    assert r.json()["ready"] is True, r.json()
    assert world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check").json()["ready"] is True
    world.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
    phases = []
    for _ in range(60):
        agent._last_mission = 0
        agent.step()
        phases.append(sim.phase)
        if world.status(o["id"]) != "in_progress":
            break
    assert world.status(o["id"]) == "awaiting_delivery_confirmation"
    tel = world.operator.get(f"/api/v1/orders/{o['id']}/telemetry?limit=600").json()["items"]
    first = next(t for t in tel if t["flight_phase"] in ("takeoff", "cruise"))
    from conftest import BASE
    assert abs(first["lat"] - BASE["lat"]) < 0.01                # вылет с базы парка
    assert "pickup" in phases and phases.index("pickup") < phases.index("delivery")
    log = world.admin.get("/api/v1/admin/audit?action=order.cargo_picked_up").json()
    assert any(e["entity_id"] == o["id"] for e in log["items"])


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


# --- источники видео дрона: USB, RTSP, тестовые ролики; выбор оператором ---

import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import time as _time  # noqa: E402

from fpv_agent.video import VideoManager, discover  # noqa: E402
from fpv_agent.__main__ import main as agent_main  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not _shutil.which("ffmpeg"), reason="нет ffmpeg")


def _fake_v4l(tmp_path):
    sysfs, dev = tmp_path / "sys", tmp_path / "dev"
    for node, index, name in (("video0", "0", "USB Camera"), ("video1", "1", "USB Camera"), ("video2", "0", "Capture")):
        (sysfs / node).mkdir(parents=True)
        (sysfs / node / "index").write_text(index)
        (sysfs / node / "name").write_text(name)
    dev.mkdir()
    for node in ("video0", "video1"):           # video2 — в sysfs есть, устройства нет
        (dev / node).touch()
    return sysfs, dev


def _make_clip(path):
    _subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
                     "-t", "3", "-c:v", "mpeg4", str(path)], check=True)


def test_video_sources_discovery(tmp_path):
    sysfs, dev = _fake_v4l(tmp_path)
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "flight.mp4").write_bytes(b"x")
    (videos / "notes.txt").write_text("x")
    (videos / "bad name.mp4").write_bytes(b"x")
    cams = tmp_path / "cameras.conf"
    cams.write_text("# комментарий\nnose=rtsp://user:secret@10.0.0.5:554/stream1\nbad name=rtsp://x\nhttp=http://x/\n")
    found = discover(videos, cams, sysfs, dev)
    ids = [s.id for s in found]
    assert ids == ["usb:video0", "rtsp:nose", "file:flight.mp4", "test"]
    pub = [s.public() for s in found]
    assert "secret" not in repr(pub) and "10.0.0.5" not in repr(pub)     # адреса RTSP остаются на модуле
    assert pub[0]["label"] == "USB Camera (video0)"


@needs_ffmpeg
def test_video_manager_plays_file_and_switches(tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    _make_clip(videos / "clip.mp4")
    vm = VideoManager(videos, tmp_path / "none.conf", size="320x240", fps=10,
                      sys_v4l=tmp_path / "nosys", dev=tmp_path / "nodev")
    assert [s["id"] for s in vm.inventory()["sources"]] == ["file:clip.mp4", "test"]

    def collect(n):
        got, deadline = [], _time.monotonic() + 20
        while len(got) < n and _time.monotonic() < deadline:
            seg = vm.read_segment()
            if seg:
                got.append(seg)
            _time.sleep(0.2)
        return got

    assert len(collect(2)) == 2 and vm.active_id == "test"          # по умолчанию — таблица
    vm.select("file:clip.mp4")
    segs = collect(4)                                               # ролик 3 с — идёт по кругу
    assert len(segs) == 4 and all(s[0] == 0x47 for s in segs) and vm.active_id == "file:clip.mp4"
    vm.select("usb:video9")                                         # неизвестный — остаётся по умолчанию
    collect(1)
    assert vm.active_id == "test"
    vm.stop()
    assert vm.active_id is None


def test_operator_selects_video_source_end_to_end(world, tmp_path):
    o = world.ready_order()
    client = InProcessClient(world.app, world.drone_key)
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "demo.mp4").write_bytes(b"x")
    vm = VideoManager(videos, tmp_path / "none.conf", sys_v4l=tmp_path / "nosys", dev=tmp_path / "nodev")
    agent = ServiceModule(SimulatedDrone(video=vm), client, tmp_path / "state")
    agent.step()
    assert "/drone-link/v1/video/sources" in client.calls
    url = f"/api/v1/orders/{o['id']}/video-source"
    info = world.operator.get(url).json()
    assert [s["id"] for s in info["sources"]] == ["file:demo.mp4", "test"] and info["can_select"] is True
    # Не из списка модуля — отказ; координатор и заказчик выбирать не могут.
    assert world.operator.put(url, json={"source_id": "file:../../etc/passwd"}).status_code == 422
    assert world.operator.put(url, json={"source_id": "rtsp:other"}).status_code == 422
    assert world.coordinator.put(url, json={"source_id": "test"}).status_code in (403, 404)
    assert world.customer.get(url).status_code in (403, 404)
    r = world.operator.put(url, json={"source_id": "file:demo.mp4"})
    assert r.status_code == 200 and r.json()["selected"] == "file:demo.mp4"
    agent._last_mission = 0
    agent.step()
    assert agent.mission.video_source == "file:demo.mp4" and vm.selected == "file:demo.mp4"
    log = world.admin.get("/api/v1/admin/audit?action=drone.video_source.selected").json()
    assert any(e["action"] == "drone.video_source.selected" for e in log.get("items", log if isinstance(log, list) else []))


def test_drone_link_skips_bad_sources_keeps_good(world):
    body = {"sources": [{"id": "usb:../x", "kind": "usb", "label": ""},
                        {"id": "file:a.mp4", "kind": "usb", "label": ""},
                        {"id": "rtsp:nose", "kind": "rtsp", "label": "RTSP: nose"},
                        {"id": "rtsp:nose", "kind": "rtsp", "label": "повтор"},
                        {"id": "test", "kind": "test", "label": "Тестовая таблица"}],
            "active": "usb:video0"}
    r = world.drone.client.post("/drone-link/v1/video/sources", json=body, headers=world.drone.headers)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 2 and len(r.json()["skipped"]) == 3 and r.json()["active"] is None
    conn = world.technician.get(f"/api/v1/drones/{world.drone_id}/connection").json()
    assert [s["id"] for s in conn["video"]["sources"]] == ["rtsp:nose", "test"]
    r = world.drone.client.post("/drone-link/v1/video/sources", json={"sources": "x"}, headers=world.drone.headers)
    assert r.status_code == 422


def test_cameras_conf_duplicate_names(tmp_path):
    cams = tmp_path / "cameras.conf"
    cams.write_text("nose=rtsp://10.0.0.1/a\nnose=rtsp://10.0.0.2/b\n")
    found = discover(tmp_path / "novideos", cams, tmp_path / "nosys", tmp_path / "nodev")
    assert [s.id for s in found] == ["rtsp:nose", "test"] and found[0].target.endswith("/a")


@pytest.mark.parametrize("args", [["--video-device", "/dev/sda"], ["--video-device", "/dev/video0;rm"],
                                  ["--video-device", "/dev/video0", "--video-size", "big"],
                                  ["--video-device", "/dev/video0", "--video-fps", "0"]])
def test_agent_rejects_bad_video_args(args, monkeypatch):
    monkeypatch.setenv("FPV_DRONE_KEY", "x")
    assert agent_main(["--server", "http://127.0.0.1:9", *args]) == 2


def test_inventory_sent_on_first_step_regardless_of_uptime(world, tmp_path, monkeypatch):
    """Сразу после загрузки ОС time.monotonic() мал — первая отправка списка не должна от него зависеть."""
    import fpv_agent.agent as agent_mod
    monkeypatch.setattr(agent_mod.time, "monotonic", lambda: 12.0)
    client = InProcessClient(world.app, world.drone_key)
    vm = VideoManager(tmp_path / "v", tmp_path / "none.conf", sys_v4l=tmp_path / "nosys", dev=tmp_path / "nodev")
    ServiceModule(SimulatedDrone(video=vm), client, tmp_path / "state").step()
    assert "/drone-link/v1/video/sources" in client.calls


def test_inventory_retried_after_server_5xx(world, tmp_path):
    """502 от nginx во время перезапуска API — список отправляется снова на следующем такте."""
    client = InProcessClient(world.app, world.drone_key)
    real = client.request
    state = {"fail": True}

    def flaky(method, path, body=None, **kw):
        if path == "/drone-link/v1/video/sources" and state["fail"]:
            state["fail"] = False
            client.calls.append(path)
            return {"_status": 502, "raw": "Bad Gateway"}
        return real(method, path, body, **kw)

    client.request = flaky
    vm = VideoManager(tmp_path / "v", tmp_path / "none.conf", sys_v4l=tmp_path / "nosys", dev=tmp_path / "nodev")
    agent = ServiceModule(SimulatedDrone(video=vm), client, tmp_path / "state")
    agent.step()
    assert agent._last_inventory is None
    agent.step()
    assert client.calls.count("/drone-link/v1/video/sources") == 2
    conn = world.technician.get(f"/api/v1/drones/{world.drone_id}/connection").json()
    assert [x["id"] for x in conn["video"]["sources"]] == ["test"]
