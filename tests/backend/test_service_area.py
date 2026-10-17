"""Зона обслуживания: настройка администратором в интерфейсе вместо файла на сервере."""
from __future__ import annotations

from conftest import BASE

HELSINKI = {"south": 59.9, "west": 24.3, "north": 60.6, "east": 25.6}
ESPOO_BASE = {"lat": 60.21007, "lon": 24.823546, "address": "Эспоо, база"}


def test_default_comes_from_config(world):
    a = world.customer.get("/api/v1/service-area").json()
    assert a["source"] == "config" and a["south"] == 55.55 and a["east"] == 37.9


def test_admin_moves_area_and_fleet_base_follows(world):
    r = world.technician.post("/api/v1/fleets", json={"name": "Хельсинки", "base": ESPOO_BASE})
    assert r.status_code == 422 and "Зона обслуживания" in r.json()["error"]["message"]
    # в новой зоне не окажется базы существующего московского парка — нужно подтверждение
    r = world.admin.put("/api/v1/admin/service-area", json=HELSINKI)
    assert r.status_code == 409 and r.json()["error"]["code"] == "fleets_outside"
    assert r.json()["error"]["details"]["fleets"][0]["lat"] == BASE["lat"]
    r = world.admin.put("/api/v1/admin/service-area", json={**HELSINKI, "confirm": True})
    assert r.status_code == 200 and r.json()["source"] == "admin" and r.json()["north"] == 60.6
    assert world.technician.post("/api/v1/fleets", json={"name": "Хельсинки", "base": ESPOO_BASE}).status_code == 201
    # заказ по московскому маршруту теперь не проходит проверку
    o = world.create_order()
    s = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()
    assert "service_area" in [c["code"] for c in s["validation_result"]["checks"] if not c["ok"]]
    audit = world.admin.get("/api/v1/admin/audit?action=platform.service_area_changed").json()["items"]
    assert audit[0]["details"]["after"]["north"] == 60.6 and audit[0]["details"]["fleets_outside"]
    # возврат к значению из настроек сервера
    r = world.admin.delete("/api/v1/admin/service-area")
    assert r.status_code == 200 and r.json()["source"] == "config"
    assert world.admin.delete("/api/v1/admin/service-area").status_code == 404


def test_area_validation_and_permissions(world):
    bad = [{"south": 60.6, "west": 24.3, "north": 59.9, "east": 25.6},
           {"south": 59.9, "west": 25.6, "north": 60.6, "east": 24.3},
           {"south": 10.0, "west": 0.0, "north": 60.0, "east": 30.0}]
    for b in bad:
        assert world.admin.put("/api/v1/admin/service-area", json=b).status_code == 422
    big_enough = {"south": 55.0, "west": 24.0, "north": 61.0, "east": 38.5}   # и Москва, и Хельсинки
    assert world.admin.put("/api/v1/admin/service-area", json=big_enough).status_code == 200
    for who in (world.customer, world.technician, world.operator):
        assert who.put("/api/v1/admin/service-area", json=big_enough).status_code == 403
