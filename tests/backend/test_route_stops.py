"""Промежуточные точки маршрута заказчика: А → Б → [1, 2 …] → В."""
from __future__ import annotations

from conftest import BASE, DEST, ORIGIN

STOP1 = {"lat": 55.7700, "lon": 37.6100, "address": "Аптека, ул. Лесная, 2", "note": "забрать рецепт"}
STOP2 = {"lat": 55.7650, "lon": 37.6250, "address": "Почта, ул. Речная, 7"}


def test_order_with_stops(world):
    plain = world.create_order()
    o = world.create_order(stops=[STOP1, STOP2])
    r = o["route"]
    assert [s["address"] for s in r["stops"]] == [STOP1["address"], STOP2["address"]]
    assert r["stops"][0]["note"] == "забрать рецепт" and r["stops"][1]["note"] == ""
    assert r["distance_m"] > plain["route"]["distance_m"]                    # путь через точки длиннее
    kinds = [c["kind"] for c in r["checkpoints"]]
    assert kinds.count("stop") == 2 and kinds.index("pickup") < kinds.index("stop")
    names = [c["name"] for c in r["checkpoints"] if c["kind"] == "stop"]
    assert names == ["1 — Аптека, ул. Лесная, 2", "2 — Почта, ул. Речная, 7"]
    # проверка условий и цена учитывают промежуточные точки
    s = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()
    assert s["status"] == "pending_confirmation", s["validation_result"]
    p = world.customer.post(f"/api/v1/orders/{plain['id']}/submit", json={}).json()
    assert float(s["quote_amount"]) > float(p["quote_amount"])


def test_stop_outside_service_area_fails_validation(world):
    o = world.create_order(stops=[{"lat": 10.0, "lon": 10.0, "address": "Очень далеко"}])
    s = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()
    failed = [c["code"] for c in s["validation_result"]["checks"] if not c["ok"]]
    assert "service_area" in failed and s["status"] == "draft"


def test_stops_limit(world):
    r = world.customer.post("/api/v1/orders", json=world.order_payload(stops=[STOP2] * 6))
    assert r.status_code == 422
    r = world.customer.post("/api/v1/orders", json=world.order_payload(stops=[{"lat": 55.76, "lon": 37.6}]))
    assert r.status_code == 422                                              # адрес обязателен


def test_drone_mission_carries_stops(world):
    o = world.create_order(stops=[STOP1])
    assert o["route"]["base"]["lat"] == BASE["lat"]
    world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
    world.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={})
    world.coordinator_approve(o["id"])
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    world.tech_confirm(o["id"])
    world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
    m = world.drone.client.get("/drone-link/v1/mission", headers=world.drone.headers).json()
    assert m["route"]["stops"] == [{"lat": STOP1["lat"], "lon": STOP1["lon"]}]
    assert m["route"]["origin"]["lat"] == ORIGIN["lat"] and m["route"]["destination"]["lat"] == DEST["lat"]
