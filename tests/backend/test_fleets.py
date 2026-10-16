"""Парки дронов: техник ведёт свой парк, подтверждает готовность; маршрут А (база) → Б → В."""
from __future__ import annotations

from conftest import BASE, DEST, ORIGIN


def test_fleet_drone_flow_and_isolation(world, api):
    t2 = api("technician2")
    # Чужой парк и дроны чужого парка технику недоступны.
    assert t2.get("/api/v1/fleets").json()["items"] == []
    assert t2.get(f"/api/v1/fleets/{world.fleet_id}/drones").status_code == 404
    for path in ("connection",):
        assert t2.get(f"/api/v1/drones/{world.drone_id}/{path}").status_code == 404
    assert t2.post(f"/api/v1/drones/{world.drone_id}/publish", json={"published": False}).status_code == 404
    assert t2.post(f"/api/v1/drones/{world.drone_id}/key").status_code == 404
    assert t2.post(f"/api/v1/drones/{world.drone_id}/video-check").status_code == 404
    r = t2.post("/api/v1/drones", json={"serial_number": "SN-T2", "registration_number": "RU-T2",
                                         "manufacturer": "Maker", "model": "X", "max_payload_g": 500,
                                         "max_range_m": 5000, "fleet_id": world.fleet_id})
    assert r.status_code == 404                         # заводить дрон в чужой парк нельзя

    fleets = world.technician.get("/api/v1/fleets").json()["items"]
    assert [f["id"] for f in fleets] == [world.fleet_id] and fleets[0]["drone_count"] == 1
    assert fleets[0]["base"]["address"] == BASE["address"]
    # Администратор видит все парки.
    assert any(f["id"] == world.fleet_id for f in world.admin.get("/api/v1/fleets").json()["items"])


def test_publish_requires_registration_inspection_and_key(world):
    r = world.technician.post("/api/v1/drones", json={
        "serial_number": "SN-NEW", "registration_number": "RU-NEW", "manufacturer": "Maker", "model": "X",
        "max_payload_g": 800, "max_range_m": 8000, "fleet_id": world.fleet_id})
    assert r.status_code == 201
    d = r.json()["id"]
    r = world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True})
    assert r.status_code == 409 and "администратором" in r.json()["error"]["message"]
    world.admin.post(f"/api/v1/drones/{d}/decision", json={"action": "approve"})
    r = world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True})
    assert r.status_code == 409 and "исправным" in r.json()["error"]["message"]
    world.technician.post(f"/api/v1/drones/{d}/inspections",
                          json={"result": "pass", "set_status": "serviceable", "battery_pct": 90, "comment": "ok"})
    r = world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True})
    assert r.status_code == 409 and "ключ" in r.json()["error"]["message"]
    assert world.technician.post(f"/api/v1/drones/{d}/key").status_code == 200
    assert world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True}).status_code == 200
    ids = [x["id"] for x in world.customer.get("/api/v1/available-drones").json()["items"]]
    assert d in ids and world.drone_id in ids
    item = next(x for x in world.customer.get("/api/v1/available-drones").json()["items"] if x["id"] == d)
    assert item["fleet"]["base"]["lat"] == BASE["lat"] and "api_key" not in str(item)


def test_route_base_pickup_recipient(world):
    o = world.create_order()
    r = o["route"]
    assert r["base"]["address"] == BASE["address"]
    assert r["origin"]["address"] == ORIGIN["address"] and r["destination"]["address"] == DEST["address"]
    kinds = [c.get("kind") for c in r["checkpoints"]]
    assert kinds[:2] == ["checkpoint", "pickup"] and r["checkpoints"][1]["lat"] == ORIGIN["lat"]
    assert r["distance_m"] > 3000 and r["return_m"] > 2000          # А→Б→В и возврат на базу
    # Заказ виден технику парка, но не чужому.
    assert world.technician.get(f"/api/v1/orders/{o['id']}").status_code == 200


def test_tech_ready_requires_link_telemetry_and_video(world, fake_clock):
    o = world.placed_order()
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert [x["id"] for x in world.technician.get("/api/v1/fleet-orders").json()["items"]] == [o["id"]]
    r = world.technician.post(f"/api/v1/orders/{o['id']}/tech-ready")
    assert r.status_code == 200 and r.json()["ready"] is False
    assert any("Видео с дрона не проверено" in x for x in r.json()["reasons"])
    # Без проверки видео сегменты без заказа не принимаются.
    assert world.drone.segment(1).status_code == 409
    r = world.technician.post(f"/api/v1/drones/{world.drone_id}/video-check")
    assert r.status_code == 200 and r.json()["video"]["check_active"] is True
    assert world.drone.segment(2).json()["check"] is True
    conn = world.technician.get(f"/api/v1/drones/{world.drone_id}/connection").json()
    assert conn["video"]["receiving"] is True and conn["telemetry"] is not None
    m = world.drone.client.get("/drone-link/v1/mission", headers=world.drone.headers).json()
    assert m["video_check"] is True
    # Связь пропала — подтверждение не проходит.
    fake_clock.advance(minutes=2)
    r = world.technician.post(f"/api/v1/orders/{o['id']}/tech-ready")
    assert r.json()["ready"] is False and any("Нет связи" in x for x in r.json()["reasons"])
    world.drone.telemetry()
    r = world.technician.post(f"/api/v1/orders/{o['id']}/tech-ready")
    assert r.json()["ready"] is True and r.json()["order"]["tech_ready_at"]
    # Оператор проходит проверку; отзыв подтверждения техником возвращает отказ.
    assert world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check").json()["ready"] is True
    r = world.technician.post(f"/api/v1/orders/{o['id']}/tech-ready/revoke", json={"reason": "Замена винтов"})
    assert r.status_code == 200 and r.json()["tech_ready_at"] is None
    r = world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check").json()
    assert r["ready"] is False and any("Техник парка" in x for x in r["reasons"])
    # Оператор и заказчик не подтверждают готовность за техника.
    assert world.operator.post(f"/api/v1/orders/{o['id']}/tech-ready").status_code == 403
    assert world.customer.post(f"/api/v1/orders/{o['id']}/tech-ready").status_code == 403


def test_check_live_endpoints_restricted(world):
    world.technician.post(f"/api/v1/drones/{world.drone_id}/video-check")
    world.drone.segment(5)
    url = f"/api/v1/drones/{world.drone_id}/live/check"
    for client in (world.customer, world.operator, world.coordinator):
        assert client.post(url, json={}).status_code in (403, 404)
    r = world.technician.delete(f"/api/v1/drones/{world.drone_id}/video-check")
    assert r.status_code == 200 and r.json()["video"]["check_active"] is False
    assert world.drone.segment(6).status_code == 409


def test_admin_moves_drone_without_fleet_into_fleet(world):
    # Дрон без парка (например, зарегистрированный заказчиком до версии с парками).
    import uuid
    from fpv import db as dbmod
    from fpv.models import Drone
    with dbmod.new_session() as s:
        s.get(Drone, uuid.UUID(world.drone_id)).fleet_id = None
        s.commit()
    r = world.customer.post("/api/v1/orders", json=world.order_payload())
    assert r.status_code == 422 and r.json()["error"]["code"] == "drone_invalid"
    assert world.technician.post(f"/api/v1/drones/{world.drone_id}/fleet",
                                 json={"fleet_id": world.fleet_id}).status_code == 403
    r = world.admin.post(f"/api/v1/drones/{world.drone_id}/fleet", json={"fleet_id": world.fleet_id})
    assert r.status_code == 200 and r.json()["fleet_id"] == world.fleet_id and r.json()["published"] is False
    assert world.technician.post(f"/api/v1/drones/{world.drone_id}/publish", json={"published": True}).status_code == 200
    assert world.customer.post("/api/v1/orders", json=world.order_payload()).status_code == 201
