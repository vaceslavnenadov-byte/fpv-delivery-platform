"""Онлайн-карты по API-ключу: подключение администратором, прокси плиток, выбор карты в заказе."""
from __future__ import annotations

import socket
import struct

import pytest

from fpv import runtime
from fpv.services import map_providers as mp

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 256, 256) + b"\x08\x06\x00\x00\x00" + b"\x00" * 64


@pytest.fixture()
def net(monkeypatch):
    """Подмена сети: запоминает адреса, отвечает плиткой PNG (или заданным ответом)."""
    calls = []
    state = {"reply": (200, "image/png", PNG)}

    def fake(url, data=None, headers=None, timeout=8.0):
        calls.append(url)
        if "createSession" in url:
            return 200, "application/json", b'{"session": "SESS1", "expiry": "9999999999"}'
        return state["reply"]
    monkeypatch.setattr(mp, "http_request", fake)
    monkeypatch.setattr(mp, "_check_host", lambda url, settings: None)
    mp._cache.clear()
    mp._rate.clear()
    mp._google_sessions.clear()
    return calls, state


def _add(world, **body):
    return world.admin.post("/api/v1/admin/map-providers", json=body)


def test_admin_adds_provider_key_is_never_exposed(world, net):
    assert _add(world, preset="yandex_map").status_code == 422              # ключ обязателен
    r = _add(world, preset="yandex_map", api_key="ya-secret-key-1234")
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["api_key_masked"].endswith("1234") and "ya-secret" not in r.text
    assert m["projection"] == "3395" and "tiles.api-maps.yandex.ru" in m["url_template"]
    items = world.customer.get("/api/v1/map-providers").json()["items"]
    assert [x["name"] for x in items] == ["Яндекс — схема"]
    assert "url_template" not in items[0] and "api_key_masked" not in items[0]
    admin_list = world.admin.get("/api/v1/admin/map-providers").json()
    assert "ya-secret" not in str(admin_list) and any(p["code"] == "esri_imagery" for p in admin_list["presets"])
    assert world.customer.post("/api/v1/admin/map-providers", json={"preset": "osm"}).status_code == 403


def test_tile_proxy_and_cache(world, net):
    calls, _ = net
    m = _add(world, preset="yandex_map", api_key="ya-key-5678").json()
    url = f"/api/v1/map-providers/{m['id']}/tiles/12/2476/1281"
    r = world.customer.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert calls == ["https://tiles.api-maps.yandex.ru/v1/tiles/?x=2476&y=1281&z=12&lang=ru_RU&l=map&apikey=ya-key-5678"]
    assert world.operator.get(url).status_code == 200 and len(calls) == 1     # из кеша
    assert world.customer.get(f"/api/v1/map-providers/{m['id']}/tiles/3/9/1").status_code == 404   # вне сетки


def test_upstream_errors_are_reported(world, net):
    _, state = net
    m = _add(world, preset="esri_imagery", api_key="arcgis-token").json()
    state["reply"] = (403, "application/json", b'{"error": "Invalid token"}')
    r = world.customer.get(f"/api/v1/map-providers/{m['id']}/tiles/10/619/320")
    assert r.status_code == 502 and "отклонил ключ" in r.json()["error"]["message"]
    err = world.admin.get("/api/v1/admin/map-providers").json()["items"][0]["last_error"]
    assert "Invalid token" in err
    state["reply"] = (200, "text/html", b"<html>login</html>")
    r = world.admin.post(f"/api/v1/admin/map-providers/{m['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "не изображение" in r.json()["message"]
    state["reply"] = (200, "image/png", PNG)
    r = world.admin.post(f"/api/v1/admin/map-providers/{m['id']}/test").json()
    assert r["ok"] is True
    assert world.admin.get("/api/v1/admin/map-providers").json()["items"][0]["last_error"] is None


def test_google_session(world, net):
    calls, _ = net
    m = _add(world, preset="google_satellite", api_key="AIzaKEY").json()
    for x in (1, 2):
        assert world.customer.get(f"/api/v1/map-providers/{m['id']}/tiles/5/{x}/3").status_code == 200
    assert sum("createSession" in c for c in calls) == 1                     # сеанс переиспользуется
    assert calls[-1] == "https://tile.googleapis.com/v1/2dtiles/5/2/3?session=SESS1&key=AIzaKEY"


def test_rate_limit(world, net, monkeypatch):
    monkeypatch.setattr(runtime.settings(), "map_tile_rate_per_min", 2)
    m = _add(world, preset="osm").json()
    codes = [world.customer.get(f"/api/v1/map-providers/{m['id']}/tiles/4/{x}/5").status_code for x in range(3)]
    assert codes == [200, 200, 429]


def test_template_validation_and_private_hosts(world, net, monkeypatch):
    assert _add(world, preset="custom", url_template="http://tiles.example/{z}/{x}/{y}.png").status_code == 422
    assert _add(world, preset="custom", url_template="https://tiles.example/{z}/{x}.png").status_code == 422
    assert _add(world, preset="custom", url_template="https://tiles.example/{z}/{x}/{y}.png?k={key}").status_code == 422
    m = _add(world, preset="custom", name="Внутренний сервер", url_template="https://tiles.example/{z}/{x}/{y}.png").json()
    monkeypatch.undo()                                                       # настоящая проверка адреса
    monkeypatch.setattr(mp, "http_request", lambda *a, **k: (200, "image/png", PNG))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    mp._cache.clear()
    r = world.customer.get(f"/api/v1/map-providers/{m['id']}/tiles/4/1/1")
    assert r.status_code == 502 and "внутренней сети" in r.json()["error"]["message"]


def test_order_keeps_customer_map(world, net):
    m = _add(world, preset="osm").json()
    o = world.create_order(map_provider_id=m["id"])
    assert o["map_provider_id"] == m["id"]
    assert world.operator.get(f"/api/v1/orders/{o['id']}").status_code in (200, 404)
    world.admin.patch(f"/api/v1/admin/map-providers/{m['id']}", json={"active": False})
    r = world.customer.post("/api/v1/orders", json=world.order_payload(map_provider_id=m["id"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "map_provider_invalid"
    assert world.admin.delete(f"/api/v1/admin/map-providers/{m['id']}").status_code == 204
    assert world.customer.get(f"/api/v1/orders/{o['id']}").json()["map_provider_id"] is None
