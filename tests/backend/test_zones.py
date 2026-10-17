"""Несколько зон обслуживания и назначение в них техников."""
from __future__ import annotations

from conftest import BASE

HELSINKI = {"name": "Хельсинки", "south": 59.9, "west": 24.3, "north": 60.6, "east": 25.6}
ESPOO_BASE = {"lat": 60.21007, "lon": 24.823546, "address": "Эспоо, база"}


def _zones(client):
    return {z["name"]: z for z in client.get("/api/v1/service-zones").json()["items"]}


def test_default_zone_is_seeded_and_open(world):
    z = _zones(world.admin)["Основная зона"]
    assert z["south"] == 55.55 and z["open_to_all"] is True
    assert [f["name"] for f in z["fleets"]] == ["Парк «Центр»"]                # парк мира привязан к зоне
    assert "Основная зона" in _zones(world.customer) and "Основная зона" in _zones(world.technician)


def test_many_zones_many_technicians(world, api):
    tech2 = api("technician2")
    r = world.admin.post("/api/v1/admin/service-zones", json=HELSINKI)
    assert r.status_code == 201, r.text
    zid = r.json()["id"]
    assert world.admin.post("/api/v1/admin/service-zones", json=HELSINKI).status_code == 409   # имя занято
    # зона без техников открыта всем
    assert world.technician.post("/api/v1/fleets", json={"name": "Север-1", "base": ESPOO_BASE}).status_code == 201
    # назначили одного техника — зона закрыта для остальных
    url = f"/api/v1/admin/service-zones/{zid}/technicians"
    assert world.admin.put(f"{url}/{tech2.user['id']}").status_code == 200
    r = world.technician.post("/api/v1/fleets", json={"name": "Север-2", "base": ESPOO_BASE})
    assert r.status_code == 422 and "«Основная зона»" in r.json()["error"]["message"]
    r = tech2.post("/api/v1/fleets", json={"name": "Север-3", "base": ESPOO_BASE})
    assert r.status_code == 201 and r.json()["zone_id"] == zid
    # в зоне может быть сколько угодно техников
    z = world.admin.put(f"{url}/{world.technician.user['id']}").json()
    assert {t["username"] for t in z["technicians"]} == {"technician", "technician2"} and not z["open_to_all"]
    assert world.technician.post("/api/v1/fleets", json={"name": "Север-4", "base": ESPOO_BASE}).status_code == 201
    assert {f["name"] for f in z["fleets"]} >= {"Север-1", "Север-3"}
    # технику — только его зоны
    assert set(_zones(tech2)) == {"Основная зона", "Хельсинки"}
    world.admin.delete(f"{url}/{world.technician.user['id']}")
    assert "Хельсинки" not in _zones(world.technician)
    # назначить можно только техника
    assert world.admin.put(f"{url}/{world.customer.user['id']}").status_code == 422
    audit = world.admin.get("/api/v1/admin/audit?action=service_zone.technician_assigned").json()["items"]
    assert len(audit) == 2


def test_zone_bounds_and_orders(world):
    main = _zones(world.admin)["Основная зона"]
    url = f"/api/v1/admin/service-zones/{main['id']}"
    moved = {"south": 55.77, "north": 55.9}                     # база парка (55.745) выпадает из зоны
    r = world.admin.patch(url, json=moved)
    assert r.status_code == 409 and r.json()["error"]["details"]["fleets"][0]["name"] == "Парк «Центр»"
    assert world.admin.patch(url, json={**moved, "confirm": True}).status_code == 200
    o = world.create_order()
    s = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()
    failed = {c["code"]: c["message"] for c in s["validation_result"]["checks"] if not c["ok"]}
    assert "service_area" in failed and "ни в одну зону" in failed["service_area"]
    # вернули границы — заказ проходит и называет зону
    assert world.admin.patch(url, json={"south": 55.55, "north": 55.95}).status_code == 200
    o = world.create_order()
    s = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()
    ok = {c["code"]: c["message"] for c in s["validation_result"]["checks"]}
    assert s["status"] == "pending_confirmation" and "«Основная зона»" in ok["service_area"]


def test_delete_zone_and_permissions(world):
    main = _zones(world.admin)["Основная зона"]
    r = world.admin.delete(f"/api/v1/admin/service-zones/{main['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "fleets_outside"
    r = world.admin.post("/api/v1/admin/service-zones", json={"name": "Москва-2", "south": 55.6, "west": 37.4,
                                                               "north": 55.9, "east": 37.8})
    assert r.status_code == 201
    assert world.admin.delete(f"/api/v1/admin/service-zones/{main['id']}?confirm=true").status_code == 204
    z = _zones(world.admin)
    assert set(z) == {"Москва-2"} and [f["name"] for f in z["Москва-2"]["fleets"]] == ["Парк «Центр»"]
    for who in (world.customer, world.technician, world.operator):
        assert who.post("/api/v1/admin/service-zones", json=HELSINKI).status_code == 403
    bad = {**HELSINKI, "name": "Ошибка", "south": 61.0}
    assert world.admin.post("/api/v1/admin/service-zones", json=bad).status_code == 422
    assert BASE["lat"] == 55.745
