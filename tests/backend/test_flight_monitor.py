"""Монитор полёта: переписка оператора и координатора, кадр с камеры, расширенная телеметрия."""
from __future__ import annotations

import shutil
import subprocess

import pytest
from sqlalchemy import text

from fpv import db as dbmod


def _assign_coordinator(world, order_id):
    r = world.admin.post(f"/api/v1/orders/{order_id}/coordinator",
                         json={"coordinator_id": world.coordinator.user["id"]})
    assert r.status_code == 200, r.text


def _ts_segment(tmp_path) -> bytes:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    out = tmp_path / "seg.ts"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-t", "1", "-c:v", "mpeg2video", "-f", "mpegts", str(out)], check=True)
    return out.read_bytes()


def test_operator_and_coordinator_chat(world):
    o = world.started_order()
    _assign_coordinator(world, o["id"])
    url = f"/api/v1/orders/{o['id']}/chat"
    r = world.coordinator.post(url, json={"body": "Полёт в штатном режиме. Держите высоту 80 м."})
    assert r.status_code == 201, r.text
    r = world.operator.post(url, json={"body": "Принял."})
    assert r.status_code == 201 and r.json()["has_attachment"] is False
    items = world.operator.get(f"/api/v1/orders/{o['id']}/notes").json()["items"]
    assert [(i["kind"], i["body"]) for i in items] == [
        ("chat", "Полёт в штатном режиме. Держите высоту 80 м."), ("chat", "Принял.")]
    assert items[0]["author"]["username"] == "coordinator" and items[0]["attachment_url"] is None
    titles = [n["title"] for n in world.coordinator.get("/api/v1/me/notifications").json()["items"]]
    assert any("сообщение от" in t for t in titles)
    with dbmod.get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'order.chat.message'")).scalar() == 2


def test_chat_access(world, api):
    o = world.started_order()
    other = api("coordinator2")               # вошёл после назначения — заказ не его
    url = f"/api/v1/orders/{o['id']}/chat"
    assert world.customer.post(url, json={"body": "Где груз?"}).status_code == 201      # «Связь по заказу»
    assert world.technician.post(url, json={"body": "x"}).status_code == 403
    assert other.post(url, json={"body": "x"}).status_code == 404                      # не назначен
    assert world.coordinator.post(url, json={"body": "x"}).status_code == 201          # назначен автоматически
    assert world.operator.post(url, json={"body": ""}).status_code == 422
    assert world.operator.post(url, json={"body": "x" * 2001}).status_code == 422
    assert world.admin.post(url, json={"body": "Контроль администратора"}).status_code == 201


def test_frame_attached_from_live_stream(world, fake_clock, tmp_path):
    o = world.started_order()
    fake_clock.advance(seconds=1)
    assert world.drone.segment(0, _ts_segment(tmp_path)).status_code == 200
    r = world.operator.post(f"/api/v1/orders/{o['id']}/chat",
                            json={"body": "Отправляю кадр зоны посадки", "attach_frame": True})
    assert r.status_code == 201 and r.json()["has_attachment"] is True
    note = world.operator.get(f"/api/v1/orders/{o['id']}/notes").json()["items"][-1]
    img = world.operator.get(note["attachment_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:3] == b"\xff\xd8\xff"
    assert world.admin.get(note["attachment_url"]).status_code == 200
    assert world.customer.get(note["attachment_url"]).status_code == 200    # участник «Связи по заказу»
    with dbmod.get_engine().connect() as c:
        key, sha = c.execute(text("SELECT attachment_key, attachment_sha256 FROM order_notes "
                                  "WHERE id = :i"), {"i": note["id"]}).one()
    assert key == f"chat/{o['id']}/{note['id']}.jpg" and len(sha) == 64


def test_frame_requires_active_flight(world):
    o = world.ready_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/chat", json={"body": "кадр", "attach_frame": True})
    assert r.status_code == 409
    assert world.operator.get(f"/api/v1/orders/{o['id']}/notes").json()["items"] == []   # без частичной записи


def test_extended_telemetry_whitelisted(world):
    o = world.started_order()
    r = world.drone.telemetry(heading_deg=342.0, ground_speed_mps=12.6,
                              extra={"satellites": 16, "voltage_v": 24.8, "flight_mode": "auto",
                                     "waypoint_index": 2, "waypoint_total": 4, "debug_dump": "x"})
    assert r.status_code == 200, r.text
    last = world.operator.get(f"/api/v1/orders/{o['id']}/telemetry").json()["items"][-1]
    assert last["heading_deg"] == 342.0
    assert last["extra"] == {"satellites": 16, "voltage_v": 24.8, "flight_mode": "auto",
                             "waypoint_index": 2, "waypoint_total": 4}          # неизвестные поля не выдаются


@pytest.mark.parametrize("extra", [{"nested": {"a": 1}}, {f"k{i}": i for i in range(40)}, {"s": "x" * 100}])
def test_telemetry_extra_limited(world, extra):
    world.started_order()
    assert world.drone.telemetry(extra=extra).status_code == 422


def test_order_chat_with_customer(world, fake_clock, tmp_path):
    o = world.placed_order()
    url = f"/api/v1/orders/{o['id']}/chat"
    assert world.customer.post(url, json={"body": "Заранее"}).status_code == 403          # до принятия оператором
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert world.customer.post(url, json={"body": "Груз у охраны"}).status_code == 201
    assert world.operator.post(url, json={"body": "Принято"}).status_code == 201
    # Служебная запись координатора заказчику не видна.
    r = world.coordinator.post(f"/api/v1/orders/{o['id']}/notes",
                               json={"kind": "incident", "body": "Внутренний инцидент", "severity": "warning"})
    assert r.status_code == 201, r.text
    mine = world.customer.get(f"/api/v1/orders/{o['id']}/notes").json()["items"]
    assert [n["body"] for n in mine] == ["Груз у охраны", "Принято"]
    assert [n["author_role"] for n in mine] == ["customer", "operator"]
    staff = world.coordinator.get(f"/api/v1/orders/{o['id']}/notes").json()["items"]
    assert "Внутренний инцидент" in [n["body"] for n in staff]
    # Кадр с камеры прикладывает только исполнитель.
    assert world.customer.post(url, json={"body": "кадр", "attach_frame": True}).status_code == 403
    # Уведомления о сообщении заказчика получают оператор и координатор.
    notes = world.operator.get("/api/v1/me/notifications").json()["items"]
    assert any("сообщение от" in n["title"] for n in notes)
