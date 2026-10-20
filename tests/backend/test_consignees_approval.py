"""Грузополучатели, заказ по точкам, согласование координатором, приоритеты, цепочка вылетов дрона,
проверка выполнения координатором, карта заказов, повтор пароля."""
from __future__ import annotations

from conftest import BASE, DEST, ORIGIN

from test_scoring import _deliver, _review

def _png() -> bytes:
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"\x00\xff\x00\x00" * 1
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()
P1 = {"lat": 55.7550, "lon": 37.6050, "address": "Точка на карте (55.75500, 37.60500)", "note": ""}
P2 = {"lat": DEST["lat"], "lon": DEST["lon"], "address": DEST["address"], "note": ""}


def _consignee(world, name, priority="normal", **extra):
    r = world.admin.post("/api/v1/catalog/consignees", json={"name": name, "kind": "Больница", "priority": priority,
                                                               "recommended_cargo": "medical_supplies",
                                                               "location": "ул. Лесная, 5, приёмный покой", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _points_order(world, consignee_id, **extra):
    body = world.order_payload(points=[P1, P2], consignee_id=consignee_id, **extra)
    for k in ("origin", "destination", "recipient_name", "recipient_phone", "window_start", "window_end"):
        body.pop(k)
    r = world.customer.post("/api/v1/orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _place(world, o):
    r = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
    assert r.json()["status"] == "pending_confirmation", r.json()["validation_result"]
    r = world.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={})
    assert r.json()["status"] == "awaiting_coordinator", r.text
    return r.json()


def test_consignees_catalog(world):
    a = world.admin
    c = _consignee(world, "Городская больница №1", "highest", comment="Звонить заранее")
    assert c["priority_label"] == "Наивысший приоритет" and c["recommended_cargo_name"] == "Медицинские изделия"
    assert a.post("/api/v1/catalog/consignees", json={"name": "городская больница №1"}).status_code == 409
    assert a.post("/api/v1/catalog/consignees", json={"name": "X", "recommended_cargo": "nope"}).status_code == 422
    assert a.post("/api/v1/catalog/consignees", json={"name": "X", "priority": "urgent"}).status_code == 422
    assert world.customer.post("/api/v1/catalog/consignees", json={"name": "Y"}).status_code == 403
    items = world.customer.get("/api/v1/catalog/consignees").json()
    assert [x["name"] for x in items["items"]] == ["Городская больница №1"] and "highest" in items["priorities"]
    r = a.patch(f"/api/v1/catalog/consignees/{c['id']}", json={"priority": "high", "comment": ""})
    assert r.json()["priority"] == "high" and r.json()["comment"] == ""
    # использованный в заказе — скрывается из выбора, сведения в заказе остаются
    o = _points_order(world, c["id"])
    assert a.delete(f"/api/v1/catalog/consignees/{c['id']}").status_code == 204
    assert world.customer.get("/api/v1/catalog/consignees").json()["items"] == []
    assert world.customer.get(f"/api/v1/orders/{o['id']}").json()["consignee"]["name"] == "Городская больница №1"


def test_points_order_consignee_and_asap(world):
    c = _consignee(world, "Аптека", "low")
    o = _points_order(world, c["id"], customer_comment="Позвонить на ресепшен")
    r = o["route"]
    assert r["pickup_at_base"] and r["origin"]["lat"] == BASE["lat"]          # груз — на базе (А)
    assert r["stops"][0]["lat"] == P1["lat"] and r["destination"]["lat"] == DEST["lat"]
    assert any(cp["name"].startswith("Точка Б") for cp in r["checkpoints"])
    assert o["asap"] and o["recipient_name"] == "Аптека" and o["priority_level"] == "low"
    assert o["recipient_phone"] is None and o["consignee"]["location"].startswith("ул. Лесная")
    # одна точка — она же получатель
    one = world.order_payload(points=[P2], consignee_id=c["id"])
    for k in ("origin", "destination", "recipient_name", "recipient_phone", "window_start", "window_end"):
        one.pop(k)
    r1 = world.customer.post("/api/v1/orders", json=one).json()
    assert r1["route"]["stops"] == [] and r1["route"]["destination"]["lat"] == DEST["lat"]
    # фото к комментарию → в переписку при размещении
    assert world.customer.post(f"/api/v1/orders/{o['id']}/comment-photo", content=b"not an image").status_code == 422
    assert world.customer.post(f"/api/v1/orders/{o['id']}/comment-photo", content=PNG).status_code == 200
    checks = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={}).json()["validation_result"]["checks"]
    assert next(x for x in checks if x["code"] == "time_window")["message"].startswith("Время доставки не задано")
    world.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={})
    notes = world.customer.get(f"/api/v1/orders/{o['id']}/notes").json()["items"]
    assert notes[0]["body"] == "Позвонить на ресепшен" and notes[0]["attachment_url"]
    assert world.customer.get(notes[0]["attachment_url"]).content == PNG
    assert world.customer.post(f"/api/v1/orders/{o['id']}/comment-photo", content=PNG).status_code == 409


def test_coordinator_approval_flow(world, api):
    o = _place(world, world.create_order())
    assert o["coordinator_id"] == world.coordinator.user["id"]
    # до согласования — ни операторам, ни технику
    assert world.operator.get("/api/v1/orders/queue").json()["items"] == []
    assert world.technician.get("/api/v1/fleet-orders").json()["items"] == []
    assert world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code in (404, 409)
    assert [x["id"] for x in world.coordinator.get("/api/v1/orders/approvals").json()["items"]] == [o["id"]]
    assert api("coordinator2").post(f"/api/v1/orders/{o['id']}/coordinator-decision",
                                    json={"approve": True}).status_code == 404       # не его заказ
    assert world.coordinator.post(f"/api/v1/orders/{o['id']}/coordinator-decision",
                                  json={"approve": False}).status_code == 422          # причина обязательна
    world.coordinator_approve(o["id"])
    d = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert d["coordinator_approved_at"] and d["coordinator_id"] == world.coordinator.user["id"]
    assert [x["id"] for x in world.operator.get("/api/v1/orders/queue").json()["items"]] == [o["id"]]
    # отклонение
    o2 = _place(world, world.create_order())
    who = o2["coordinator"]["username"]
    r = (world.coordinator if who == "coordinator" else api(who)).post(f"/api/v1/orders/{o2['id']}/coordinator-decision",
                               json={"approve": False, "comment": "Груз не соответствует"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected" and r.json()["closed_reason"] == "Груз не соответствует"
    # отмена заказчиком на согласовании
    o3 = _place(world, world.create_order())
    assert world.customer.post(f"/api/v1/orders/{o3['id']}/cancel", json={"reason": "передумал"}).json()["status"] == "cancelled"


def test_priority_order_and_drone_chain(world):
    low = _consignee(world, "Склад", "low")
    high = _consignee(world, "Поликлиника", "high")
    top = _consignee(world, "Реанимация", "highest")
    d2, _ = world.make_drone(world.technician, "SN-0002", "RU-0002")
    first = _points_order(world, low["id"])                               # дрон 1, поступил первым
    second = _points_order(world, top["id"])                              # дрон 1, наивысший
    other = _points_order(world, high["id"], drone_id=d2)                 # дрон 2
    for o in (first, second, other):
        _place(world, o)
        world.coordinator_approve(o["id"])
    queue = world.operator.get("/api/v1/orders/queue").json()["items"]
    assert [x["priority_level"] for x in queue] == ["highest", "high", "low"]
    by_id = {x["id"]: x for x in queue}
    # на одном дроне первым вылетает поступивший раньше
    assert by_id[second["id"]]["drone_waits_for"] == first["number"] and by_id[first["id"]]["drone_waits_for"] is None
    r = world.operator.post(f"/api/v1/orders/{second['id']}/accept", json={})
    assert r.status_code == 409 and str(first["number"]) in r.json()["error"]["message"]
    assert world.operator.post(f"/api/v1/orders/{first['id']}/accept", json={}).status_code == 200
    # заказчик и техник видят тот же порядок
    mine = [x["priority_level"] for x in world.customer.get("/api/v1/orders?as=customer").json()["items"]]
    assert mine[0] == "highest"


def test_review_by_coordinator_and_order_map(world, fake_clock):
    o = _deliver(world, fake_clock)
    assert world.admin.post(f"/api/v1/orders/{o['id']}/review", json={"approve": True}).status_code == 403
    assert [x["id"] for x in world.coordinator.get("/api/v1/orders/reviews").json()["items"]] == [o["id"]]
    detail = world.coordinator.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["video"] and "sha256" in detail["video"]                # видео своего заказа доступно
    assert _review(world, o).status_code == 200
    # карта заказов: дрон в работе с координатами
    o2 = world.started_order()
    world.drone.telemetry()
    m = world.coordinator.get("/api/v1/orders/map").json()["items"]
    item = next(x for x in m if x["id"] == o2["id"])
    assert item["operated"] and item["position"]["lat"] and item["operator"]["name"] and item["customer"]["name"]
    assert item["coordinator"]["name"] and item["drone"]["model"] == "Курьер-1" and item["cargo"][0]["name"]
    assert item["offered_operator"] is None                      # принят — предложения больше нет
    second, _ = world.make_drone(world.technician, "SN-MAP2", "RU-MAP2")
    waiting = world.create_order(drone_id=second)              # ждёт оператора — видно, кому предложен
    world.customer.post(f"/api/v1/orders/{waiting['id']}/submit", json={})
    world.customer.post(f"/api/v1/orders/{waiting['id']}/confirm", json={})
    world.coordinator_approve(waiting["id"])
    w = next(x for x in world.admin.get("/api/v1/orders/map").json()["items"] if x["id"] == waiting["id"])
    assert w["operator"] is None and w["offered_operator"]["name"]
    assert world.admin.get("/api/v1/orders/map").status_code == 200
    assert world.operator.get("/api/v1/orders/map").status_code == 403
    assert world.customer.get("/api/v1/orders/map").status_code == 403


def test_password_confirmation(world, api):
    rec = api("recruiter")
    body = {"username": "p.check", "password": "Check-pass-2026", "password_confirm": "Check-pass-2027",
            "last_name": "Проверкин", "first_name": "Пётр"}
    assert rec.post("/api/v1/people", json=body).status_code == 422
    body["password_confirm"] = "Check-pass-2026"
    uid = rec.post("/api/v1/people", json=body).json()["id"]
    r = world.admin.post(f"/api/v1/people/{uid}/credentials", json={"password": "New-pass-2026a", "password_confirm": "x"})
    assert r.status_code == 422
    r = world.admin.post(f"/api/v1/people/{uid}/credentials",
                         json={"password": "New-pass-2026a", "password_confirm": "New-pass-2026a"})
    assert r.status_code == 200
