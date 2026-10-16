"""Трансляция с камеры заказчика: публикация из браузера и просмотр исполнителями."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from fpv import db as dbmod
from fpv import runtime
from fpv.services import customer_stream, video


@pytest.fixture(scope="module")
def webm_segments(tmp_path_factory):
    """Самостоятельные WebM-сегменты по 2 с — как от MediaRecorder в браузере."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    d = tmp_path_factory.mktemp("webm")
    out = []
    for i in range(4):
        f = d / f"s{i}.webm"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15",
                        "-t", "2", "-c:v", "libvpx", "-b:v", "300k", "-deadline", "realtime", str(f)], check=True)
        out.append(f.read_bytes())
    return out


def send(api, order_id, seq, data, ctype="video/webm", duration=2000):
    return api.client.post(f"/api/v1/orders/{order_id}/customer-stream/segments", content=data,
                           headers=api._headers({"Content-Type": ctype, "X-Segment-Seq": str(seq),
                                                 "X-Segment-Duration-Ms": str(duration)}))


def actions():
    with dbmod.get_engine().connect() as c:
        return [r[0] for r in c.execute(text("SELECT action FROM audit_logs ORDER BY id"))]


def test_customer_streams_and_operator_watches(world, fake_clock, webm_segments, tmp_path):
    o = world.ready_order()                         # заказ у оператора, задание ещё не начато
    for i, seg in enumerate(webm_segments):
        fake_clock.advance(seconds=2)
        r = send(world.customer, o["id"], i, seg)
        assert r.status_code == 200 and r.json()["accepted"], r.text
    st = world.operator.get(f"/api/v1/orders/{o['id']}/customer-stream").json()
    assert st["active"] is True and st["segments"] == 4
    r = world.operator.post(f"/api/v1/orders/{o['id']}/live/customer", json={})
    assert r.status_code == 200, r.text
    assert r.json()["playlist_url"] == f"/api/v1/orders/{o['id']}/live/customer/playlist.m3u8"
    pl = world.operator.get(r.json()["playlist_url"])
    assert pl.status_code == 200 and pl.text.startswith("#EXTM3U")
    names = [ln for ln in pl.text.splitlines() if ln.endswith(".ts")]
    assert len(names) == 4 and "#EXTINF:2.000," in pl.text
    seg = world.operator.get(f"/api/v1/orders/{o['id']}/live/customer/{names[-1]}")
    assert seg.status_code == 200
    probe = tmp_path / "c.ts"
    probe.write_bytes(seg.content)
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(probe)], capture_output=True, text=True).stdout.split()
    assert set(codec) == {"h264"}
    jpg = world.operator.get(f"/api/v1/orders/{o['id']}/live/customer/frame.jpg")
    assert jpg.status_code == 200 and jpg.content[:3] == b"\xff\xd8\xff"
    acts = actions()
    assert acts.count("video.customer_stream.started") == 1     # одна трансляция — одна запись
    assert "video.live.view" in acts
    # видео дрона и видеоподтверждение не затронуты
    assert video._segments(runtime.settings(), o["id"]) == []


def test_publish_restrictions(world, fake_clock, webm_segments, api):
    placed = world.placed_order()                   # ещё не принят оператором
    assert send(world.customer, placed["id"], 0, webm_segments[0]).status_code == 409
    o = world.operator.post(f"/api/v1/orders/{placed['id']}/accept", json={}).json()
    assert send(world.operator, o["id"], 0, webm_segments[0]).status_code == 403          # не заказчик
    assert send(api("customer2"), o["id"], 0, webm_segments[0]).status_code == 404        # чужой заказ
    assert send(world.customer, o["id"], 0, b"TS" * 500).status_code == 422               # не WebM
    assert send(world.customer, o["id"], 0, webm_segments[0], ctype="text/plain").status_code == 422
    assert send(world.customer, o["id"], 0, webm_segments[0], duration=50).status_code == 422
    assert send(world.customer, o["id"], 0, webm_segments[0]).status_code == 200
    r = world.customer.client.post(f"/api/v1/orders/{o['id']}/customer-stream/segments",
                                   content=webm_segments[0],
                                   headers={"Content-Type": "video/webm", "X-Segment-Seq": "1",
                                            "X-Segment-Duration-Ms": "2000"})
    assert r.status_code == 403                     # без CSRF-токена


def test_viewers_restricted(world, fake_clock, webm_segments, api):
    o = world.ready_order()
    coord2 = api("coordinator2")              # вошёл после назначения — заказ не его
    send(world.customer, o["id"], 0, webm_segments[0])
    url = f"/api/v1/orders/{o['id']}/live/customer"
    assert world.technician.post(url, json={}).status_code == 403
    assert world.technician.get(f"/api/v1/orders/{o['id']}/customer-stream").status_code == 403   # заказ своего парка, но не камера
    assert coord2.post(url, json={}).status_code == 404                  # не назначен
    assert world.coordinator.post(url, json={}).status_code == 200       # назначен автоматически
    assert world.admin.post(url, json={}).status_code == 200


def test_stop_and_sweep_remove_video(world, fake_clock, webm_segments):
    o = world.ready_order()
    send(world.customer, o["id"], 0, webm_segments[0])
    ring = video.ring_dir(runtime.settings(), o["id"], "customer")
    assert any(ring.iterdir())
    assert world.customer.delete(f"/api/v1/orders/{o['id']}/customer-stream").status_code == 204
    assert not ring.parent.exists()
    assert world.operator.get(f"/api/v1/orders/{o['id']}/customer-stream").json()["active"] is False
    r = world.operator.get(f"/api/v1/orders/{o['id']}/live/customer/playlist.m3u8")
    assert r.status_code == 404 and not ring.parent.exists()      # зритель не создаёт буфер заново
    assert "video.customer_stream.stopped" in actions()
    # заказ закрыт — фоновая задача удаляет оставшийся буфер
    fake_clock.advance(seconds=2)
    send(world.customer, o["id"], 1, webm_segments[1])
    r = world.customer.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "Передумал"})
    assert r.status_code == 200, r.text
    with dbmod.session_scope() as s:
        assert customer_stream.sweep(s, runtime.settings()) == 1
    assert not ring.parent.exists()


def test_buffer_keeps_only_last_30_seconds(world, fake_clock, webm_segments):
    o = world.ready_order()
    for i in range(25):
        fake_clock.advance(seconds=2)
        send(world.customer, o["id"], i, webm_segments[i % 4])
    segs = video._segments(runtime.settings(), o["id"], "customer")
    assert len(segs) <= 16 and segs[-1].seq == 24
