"""Карты-подложки и прямая видеотрансляция выполняемого заказа."""
from __future__ import annotations

import shutil
import struct
import subprocess
import zlib

import pytest
from sqlalchemy import text

from fpv import db as dbmod
from fpv import runtime

BOUNDS = {"north": 55.80, "south": 55.70, "west": 37.50, "east": 37.70}


def png(w: int = 4, h: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def upload(api, data: bytes, name: str = "Район работ", **bounds):
    params = {"name": name, **BOUNDS, **bounds}
    return api.client.post("/api/v1/admin/maps", params=params, content=data,
                           headers=api._headers({"Content-Type": "application/octet-stream"}))


def audit_actions():
    with dbmod.get_engine().connect() as c:
        return [r[0] for r in c.execute(text("SELECT action FROM audit_logs ORDER BY id"))]


# --- карты ---

def test_admin_uploads_map_and_everyone_sees_it(world):
    data = png(40, 30)
    r = upload(world.admin, data)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["content_type"] == "image/png" and (m["width_px"], m["height_px"]) == (40, 30)
    assert m["bounds"] == BOUNDS
    for who in (world.customer, world.operator, world.coordinator, world.technician):
        items = who.get("/api/v1/maps").json()["items"]
        assert [i["id"] for i in items] == [m["id"]]
    img = world.customer.get(m["image_url"])
    assert img.status_code == 200 and img.content == data
    assert img.headers["content-type"] == "image/png"
    assert "map.uploaded" in audit_actions()


def test_only_admin_manages_maps(world):
    assert upload(world.customer, png()).status_code == 403
    assert upload(world.operator, png()).status_code == 403
    m = upload(world.admin, png()).json()
    assert world.technician.client.patch(f"/api/v1/admin/maps/{m['id']}", json={"active": False},
                                         headers=world.technician._headers()).status_code == 403
    assert world.customer.delete(f"/api/v1/admin/maps/{m['id']}").status_code == 403


def test_upload_requires_csrf(world):
    r = world.admin.client.post("/api/v1/admin/maps", params={"name": "x", **BOUNDS}, content=png())
    assert r.status_code == 403


@pytest.mark.parametrize("data", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b"GIF89a" + b"\x00" * 50,
    b"",
])
def test_only_raster_png_jpeg_webp_accepted(world, data):
    r = upload(world.admin, data)
    assert r.status_code == 422, r.text


def test_jpeg_and_webp_are_recognised(world):
    from fpv.services.maps import sniff_image
    jpeg = (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
            + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 300, 400) + b"\x00" * 10)
    assert sniff_image(jpeg) == ("image/jpeg", 400, 300)
    webp = b"RIFF" + b"\x00" * 4 + b"WEBPVP8X" + b"\x00" * 8 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
    assert sniff_image(webp) == ("image/webp", 640, 480)


@pytest.mark.parametrize("bounds", [
    {"north": 55.7, "south": 55.8},           # север южнее юга
    {"west": 37.7, "east": 37.5},             # запад восточнее востока
    {"north": 89.0},                          # вне допустимого диапазона
    {"south": 40.0, "north": 55.8},           # слишком большая область
])
def test_invalid_bounds_rejected(world, bounds):
    assert upload(world.admin, png(), **bounds).status_code == 422


def test_size_limit(world, monkeypatch):
    monkeypatch.setattr(runtime.settings(), "map_max_bytes", 100)
    r = upload(world.admin, png(100, 100) + b"\x00" * 200)
    assert r.status_code == 422 and r.json()["error"]["code"] == "map_too_large"


def test_deactivated_map_hidden_and_deleted_map_gone(world):
    m = upload(world.admin, png()).json()
    r = world.admin.client.patch(f"/api/v1/admin/maps/{m['id']}", json={"active": False, "name": "Старая"},
                                 headers=world.admin._headers())
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["name"] == "Старая"
    assert world.customer.get("/api/v1/maps").json()["items"] == []
    assert world.customer.get(m["image_url"]).status_code == 404
    assert [i["id"] for i in world.admin.get("/api/v1/maps?all=true").json()["items"]] == [m["id"]]
    assert world.customer.get("/api/v1/maps?all=true").json()["items"] == []   # параметр только для админа
    assert world.admin.delete(f"/api/v1/admin/maps/{m['id']}").status_code == 204
    assert world.admin.get(m["image_url"]).status_code == 404
    actions = audit_actions()
    assert "map.updated" in actions and "map.deleted" in actions


def test_patch_bounds_validated(world):
    m = upload(world.admin, png()).json()
    r = world.admin.client.patch(f"/api/v1/admin/maps/{m['id']}", json={"north": 55.0},
                                 headers=world.admin._headers())
    assert r.status_code == 422


# --- прямая трансляция ---

def _ts_segments(tmp_path, n: int):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    d = tmp_path / "segs"
    d.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-t", str(n), "-c:v", "mpeg2video", "-g", "10", "-f", "segment", "-segment_time", "1",
                    "-segment_format", "mpegts", str(d / "s%03d.ts")], check=True)
    return [f.read_bytes() for f in sorted(d.glob("*.ts"))][:n]


def _playlist_entries(text_: str):
    return [line for line in text_.splitlines() if line.endswith(".ts")]


def test_live_stream_for_customer_operator_admin(world, fake_clock, tmp_path):
    o = world.started_order()
    for i, data in enumerate(_ts_segments(tmp_path, 6)):
        fake_clock.advance(seconds=1)
        assert world.drone.segment(i, data).status_code == 200
    for who in (world.customer, world.operator, world.admin):
        r = who.post(f"/api/v1/orders/{o['id']}/live", json={})
        assert r.status_code == 200, r.text
        assert r.json()["playlist_url"] == f"/api/v1/orders/{o['id']}/live/playlist.m3u8"
    pl = world.customer.get(f"/api/v1/orders/{o['id']}/live/playlist.m3u8")
    assert pl.status_code == 200 and pl.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    body = pl.text
    assert body.startswith("#EXTM3U") and "#EXT-X-MEDIA-SEQUENCE:2" in body
    entries = _playlist_entries(body)
    assert len(entries) == 4                       # скользящее окно live_window_segments
    seg = world.customer.get(f"/api/v1/orders/{o['id']}/live/{entries[-1]}")
    assert seg.status_code == 200 and seg.headers["content-type"] == "video/mp2t"
    probe = tmp_path / "live.ts"
    probe.write_bytes(seg.content)
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name", "-of", "csv=p=0", str(probe)], capture_output=True, text=True)
    assert set(codec.stdout.split()) == {"h264"}     # браузер воспроизводит H.264, а не MPEG-2
    assert audit_actions().count("video.live.view") == 3
    jpg = world.customer.get(f"/api/v1/orders/{o['id']}/live/frame.jpg")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg" and jpg.content[:3] == b"\xff\xd8\xff"
    assert world.technician.get(f"/api/v1/orders/{o['id']}/live/frame.jpg").status_code == 403


def test_live_access_restricted(world, fake_clock, tmp_path, api):
    o = world.started_order()
    world.drone.segment(0, _ts_segments(tmp_path, 1)[0])
    url = f"/api/v1/orders/{o['id']}/live"
    assert world.technician.post(url, json={}).status_code == 403        # нет разрешения video.live
    coord2 = api("coordinator2")                                         # вошёл после назначения
    assert coord2.post(url, json={}).status_code == 404                  # не назначен на заказ
    assert coord2.get(url + "/playlist.m3u8").status_code == 404
    other = api("customer2")
    assert other.post(url, json={}).status_code == 404                   # чужой заказ
    r = world.admin.post(f"/api/v1/orders/{o['id']}/coordinator",
                         json={"coordinator_id": coord2.user["id"]})       # ручное переназначение
    assert r.status_code == 200, r.text
    assert coord2.post(url, json={}).status_code == 200                  # после назначения
    assert world.coordinator.post(url, json={}).status_code == 404       # прежний — без доступа
    assert world.customer.get(url + "/..%2F..%2Fring.ts").status_code in (404, 422)


def test_live_only_while_in_progress(world, fake_clock, tmp_path):
    o = world.ready_order()
    assert world.customer.post(f"/api/v1/orders/{o['id']}/live", json={}).status_code == 409
    assert world.operator.post(f"/api/v1/orders/{o['id']}/start", json={}).status_code == 200
    world.drone.segment(0, _ts_segments(tmp_path, 1)[0])
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    r = world.customer.get(f"/api/v1/orders/{o['id']}/live/playlist.m3u8")
    assert r.status_code == 409 and r.json()["error"]["code"] == "live_not_active"


def test_undecodable_stream_reported(world, fake_clock):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    o = world.started_order()
    for i in range(3):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)                       # не видео: синтетические байты
    r = world.customer.get(f"/api/v1/orders/{o['id']}/live/playlist.m3u8")
    assert r.status_code == 404 and r.json()["error"]["code"] == "live_no_video"


def test_gap_and_device_restart_marked_as_discontinuity(world, fake_clock, tmp_path):
    o = world.started_order()
    segs = _ts_segments(tmp_path, 4)
    for i in range(2):
        fake_clock.advance(seconds=1)
        world.drone.segment(10 + i, segs[i])
    url = f"/api/v1/orders/{o['id']}/live/playlist.m3u8"
    first = world.customer.get(url).text
    assert "#EXT-X-MEDIA-SEQUENCE:0" in first and "#EXT-X-DISCONTINUITY\n" not in first
    fake_clock.advance(seconds=8)                    # потеря связи, затем перезапуск модуля (seq с 0)
    for i in range(2):
        fake_clock.advance(seconds=1)
        world.drone.segment(i, segs[2 + i])
    body = world.customer.get(url).text
    assert "#EXT-X-MEDIA-SEQUENCE:0" in body and "#EXT-X-DISCONTINUITY\n" in body
    assert len(_playlist_entries(body)) == 4         # нумерация HLS продолжается, не сбрасывается
