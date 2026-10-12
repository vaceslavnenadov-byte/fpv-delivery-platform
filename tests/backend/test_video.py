"""Кольцевой буфер и видеоподтверждения (последние 15 секунд)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from conftest import run_outbox
from fpv import db as dbmod


def _spool_segments(settings, order_id):
    ring = Path(settings.video_spool_dir) / order_id / "ring"
    return sorted(ring.iterdir()) if ring.is_dir() else []


def test_ring_buffer_keeps_only_recent_segments(world, settings, fake_clock):
    o = world.started_order()
    for i in range(60):
        fake_clock.advance(seconds=1)
        assert world.drone.segment(i).json()["accepted"]
    files = _spool_segments(settings, o["id"])
    # 15 с буфера + 5 с запаса, остальное вытеснено.
    assert 15 <= len(files) <= 21
    state = world.operator.get(f"/api/v1/orders/{o['id']}/telemetry").json()["video_buffer"]
    assert state["segments"] == len(files)


def test_clip_contains_last_15_seconds_before_completion(world, settings, fake_clock):
    o = world.started_order()
    for i in range(40):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)
    fake_clock.advance(milliseconds=500)
    r = world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    v = r.json()["video"]
    assert v["segment_count"] == 15
    run_outbox()
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()["video"]
    assert detail["status"] == "stored" and detail["integrity_ok"] is True
    body = world.admin.get(f"/api/v1/videos/{detail['id']}/content?variant=original").content
    assert body.startswith(b"TS000025") and b"TS000039" in body and b"TS000024" not in body
    # Буфер очищен после фиксации клипа.
    assert _spool_segments(settings, o["id"]) == []


def test_segments_after_completion_are_rejected(world, fake_clock):
    o = world.started_order()
    world.drone.segment(1)
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    r = world.drone.segment(2)
    assert r.status_code == 409


def test_duplicate_segment_ignored(world, fake_clock):
    world.started_order()
    assert world.drone.segment(7).json()["accepted"] is True
    assert world.drone.segment(7).json()["duplicate"] is True


def test_missing_video_is_recorded_not_faked(world):
    o = world.started_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    v = r.json()["video"]
    assert v["status"] == "missing"
    assert "не поступал" in v["missing_reason"]
    run_outbox()
    with dbmod.get_engine().connect() as c:
        row = c.execute(text("SELECT object_key, sha256 FROM video_evidence WHERE order_id=:o"), {"o": o["id"]}).one()
    assert row == (None, None)
    # Видео отсутствует → баллы за видео не начисляются, но заказ можно проверить.
    world.customer.post(f"/api/v1/orders/{o['id']}/receipt", json={"received": True})
    r = world.admin.post(f"/api/v1/orders/{o['id']}/review",
                         json={"approve": True, "comment": "Видео нет, подтверждено по акту", "video_verified": None})
    assert r.status_code == 200
    score = world.admin.get(f"/api/v1/orders/{o['id']}/score").json()
    assert score["result"]["items"]["video_verified"] == 0
    assert score["result"]["video_status"] == "missing"


def test_stale_stream_before_event_marked_missing(world, fake_clock):
    o = world.started_order()
    for i in range(5):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)
    fake_clock.advance(seconds=40)          # поток прервался задолго до завершения
    world.drone.telemetry()
    v = world.operator.post(f"/api/v1/orders/{o['id']}/complete").json()["video"]
    assert v["status"] == "missing" and "прерван" in v["missing_reason"]


def test_cannot_approve_missing_video(world):
    o = world.started_order()
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    world.customer.post(f"/api/v1/orders/{o['id']}/receipt", json={"received": True})
    r = world.admin.post(f"/api/v1/orders/{o['id']}/review",
                         json={"approve": True, "comment": "ok", "video_verified": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "video_unavailable"


def _stored_clip(world, fake_clock):
    o = world.started_order()
    for i in range(20):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    run_outbox()
    return o, world.admin.get(f"/api/v1/orders/{o['id']}").json()["video"]


def test_tampered_file_detected(world, settings, fake_clock):
    o, v = _stored_clip(world, fake_clock)
    path = Path(settings.object_store_root) / v["object_key"]
    os.chmod(path, 0o640)
    path.write_bytes(b"forged content")
    r = world.admin.post(f"/api/v1/admin/videos/{v['id']}/verify")
    assert r.json() == {"ok": False, "status": "corrupted"}
    assert world.admin.get(f"/api/v1/videos/{v['id']}/content").status_code == 409


def test_evidence_rows_cannot_be_modified_or_deleted(world, fake_clock):
    o, v = _stored_clip(world, fake_clock)
    with dbmod.get_engine().connect() as c:
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE video_evidence SET sha256=repeat('0',64) WHERE id=:i"), {"i": v["id"]})
        c.rollback()
        with pytest.raises(DBAPIError):
            c.execute(text("DELETE FROM video_evidence WHERE id=:i"), {"i": v["id"]})
        c.rollback()


def test_object_store_refuses_overwrite(world, settings, fake_clock):
    from fpv.services.storage import FilesystemObjectStore, StorageError
    o, v = _stored_clip(world, fake_clock)
    store = FilesystemObjectStore(settings.object_store_root)
    src = Path(settings.object_store_root) / "x.bin"
    src.write_bytes(b"x")
    with pytest.raises(StorageError):
        store.put_file(v["object_key"], src, "video/mp2t")
    with pytest.raises(StorageError):
        store.put_file("../escape", src, "video/mp2t")


def test_view_is_logged_and_restricted(world, fake_clock):
    o, v = _stored_clip(world, fake_clock)
    assert world.operator.get(f"/api/v1/videos/{v['id']}/content").status_code == 403
    assert world.customer.get(f"/api/v1/videos/{v['id']}/content").status_code == 403
    assert world.admin.get(f"/api/v1/videos/{v['id']}/content").status_code == 200
    with dbmod.get_engine().connect() as c:
        actions = [r[0] for r in c.execute(text("SELECT action FROM video_access_log ORDER BY id"))]
    assert "view" in actions


def test_drone_cannot_push_video_for_foreign_or_idle_state(world, api):
    r = world.drone.segment(1)      # заказ не выполняется
    assert r.status_code == 409
    bad = world.drone.client.post("/drone-link/v1/video/segments", content=b"x",
                                  headers={"Authorization": "Drone fpvd_forged", "X-Segment-Seq": "1",
                                           "X-Segment-Duration-Ms": "1000"})
    assert bad.status_code == 403


def test_real_mpegts_clip_gets_mp4_preview(world, settings, fake_clock, tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    seg_dir = tmp_path / "segs"
    seg_dir.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-t", "18", "-c:v", "mpeg2video", "-f", "segment", "-segment_time", "1",
                    "-segment_format", "mpegts", str(seg_dir / "s%03d.ts")], check=True)
    o = world.started_order()
    for i, f in enumerate(sorted(seg_dir.glob("*.ts"))):
        fake_clock.advance(seconds=1)
        assert world.drone.segment(i, f.read_bytes()).status_code == 200
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    run_outbox()
    v = world.admin.get(f"/api/v1/orders/{o['id']}").json()["video"]
    assert v["has_preview"] is True
    preview = world.admin.get(f"/api/v1/videos/{v['id']}/content?variant=preview")
    assert preview.headers["content-type"] == "video/mp4" and len(preview.content) > 1000
