"""Справочники «Типы грузов» и «Типы БПЛА», несколько грузов в заказе, архив БПЛА, отчёт о
техсостоянии и новые поля профиля."""
from __future__ import annotations


def _items(o):
    return [(i["cargo_type"], i["weight_g"]) for i in o["cargo_items"]]


def test_cargo_types_catalog(world, api):
    a = world.admin
    names = {c["code"]: c for c in world.customer.get("/api/v1/catalog/cargo-types").json()["items"]}
    assert names["documents"]["name"] == "Документы" and names["documents"]["max_weight_g"] == 2000
    assert world.customer.post("/api/v1/catalog/cargo-types", json={"name": "X", "max_weight_g": 10}).status_code == 403
    r = a.post("/api/v1/catalog/cargo-types", json={"name": "Цветы", "category": "Подарки", "max_weight_g": 800})
    assert r.status_code == 201, r.text
    flowers = r.json()
    assert flowers["code"].startswith("c_")
    assert a.post("/api/v1/catalog/cargo-types", json={"name": "цветы", "max_weight_g": 5}).status_code == 409
    assert a.patch(f"/api/v1/catalog/cargo-types/{flowers['id']}", json={"max_weight_g": 500}).json()["max_weight_g"] == 500
    # Груз нового типа: масса не больше максимальной для типа.
    o = world.create_order(cargo_type=flowers["code"], cargo_weight_g=600)
    r = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
    check = next(c for c in r.json()["validation_result"]["checks"] if c["code"] == "cargo_weight")
    assert not check["ok"] and "Цветы" in check["message"]
    assert o["cargo_type_name"] == "Цветы"
    # Удалённый тип, встречавшийся в заказах, скрывается из выбора, но название в заказе остаётся.
    assert a.delete(f"/api/v1/catalog/cargo-types/{flowers['id']}").status_code == 204
    assert flowers["code"] not in [c["code"] for c in world.customer.get("/api/v1/catalog/cargo-types").json()["items"]]
    assert world.customer.get(f"/api/v1/orders/{o['id']}").json()["cargo_type_name"] == "Цветы"
    r = world.customer.post("/api/v1/orders", json=world.order_payload(cargo_type=flowers["code"]))
    assert r.status_code == 422
    # Неиспользованный тип удаляется полностью.
    tmp = a.post("/api/v1/catalog/cargo-types", json={"name": "Временный", "max_weight_g": 100}).json()
    assert a.delete(f"/api/v1/catalog/cargo-types/{tmp['id']}").status_code == 204
    assert a.patch(f"/api/v1/catalog/cargo-types/{tmp['id']}", json={"name": "Y"}).status_code == 404


def test_several_cargo_items_in_one_order(world):
    items = [
        {"cargo_type": "documents", "description": "Договор", "weight_g": 200, "length_mm": 300, "width_mm": 210, "height_mm": 10},
        {"cargo_type": "medical_supplies", "description": "Аптечка", "weight_g": 1500, "length_mm": 250, "width_mm": 150, "height_mm": 80},
        {"cargo_type": "parcel", "description": "Коробка", "weight_g": 1200, "length_mm": 200, "width_mm": 200, "height_mm": 200},
    ]
    body = world.order_payload(cargo_items=items)
    for k in ("cargo_type", "cargo_description", "cargo_weight_g", "cargo_length_mm", "cargo_width_mm", "cargo_height_mm"):
        body.pop(k)
    r = world.customer.post("/api/v1/orders", json=body)
    assert r.status_code == 201, r.text
    o = r.json()
    assert _items(o) == [("documents", 200), ("medical_supplies", 1500), ("parcel", 1200)]
    # Сводка: масса — сумма (больше грузоподъёмности дрона 1500 г — пока не проверяется), габариты — наибольшие.
    assert o["cargo_weight_g"] == 2900 and o["cargo_dims_mm"] == [300, 210, 200]
    assert o["cargo_items"][1]["cargo_type_name"] == "Медицинские изделия"
    r = world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
    assert r.json()["status"] == "pending_confirmation", r.json()["validation_result"]
    assert world.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={}).json()["status"] == "awaiting_operator"
    # Оператор и техник видят все грузы заказа.
    assert _items(world.operator.get(f"/api/v1/orders/{o['id']}").json()) == _items(o)
    assert _items(world.technician.get(f"/api/v1/orders/{o['id']}").json()) == _items(o)
    # Без грузов заказ не создаётся.
    body["cargo_items"] = []
    assert world.customer.post("/api/v1/orders", json=body).status_code == 422


def test_drone_models_catalog_and_registration(world):
    a, t = world.admin, world.technician
    m = a.post("/api/v1/catalog/drone-models", json={
        "model": "Курьер-2", "manufacturer": "АэроТех", "link_type": "4G/LTE", "construction": "Квадрокоптер",
        "engine_type": "Бесколлекторный", "max_range_m": 12000, "max_payload_g": 2500})
    assert m.status_code == 201, m.text
    m = m.json()
    assert a.post("/api/v1/catalog/drone-models", json={"model": "курьер-2", "manufacturer": "аэротех",
                                                        "max_range_m": 9000, "max_payload_g": 100}).status_code == 409
    assert world.customer.get("/api/v1/catalog/drone-models").status_code == 403
    assert [x["id"] for x in t.get("/api/v1/catalog/drone-models").json()["items"]] == [m["id"]]
    # Конфигурация из справочника: параметры — из справочника, номера — техника.
    r = t.post("/api/v1/drones", json={"serial_number": "SN-CAT", "registration_number": "RU-CAT", "manufacturer": "xx",
                                        "model": "yy", "max_payload_g": 100, "max_range_m": 1000,
                                        "fleet_id": world.fleet_id, "drone_model_id": m["id"]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["model"], d["manufacturer"], d["max_payload_g"], d["link_type"], d["engine_type"]) == \
        ("Курьер-2", "АэроТех", 2500, "4G/LTE", "Бесколлекторный")
    assert d["drone_model_id"] == m["id"]
    # Ввод вручную в справочник не попадает; дополнительные параметры необязательны.
    r = t.post("/api/v1/drones", json={"serial_number": "SN-MAN", "registration_number": "RU-MAN", "manufacturer": "Сам",
                                        "model": "Самоделка", "max_payload_g": 500, "max_range_m": 3000,
                                        "fleet_id": world.fleet_id, "construction": "Гексакоптер"})
    assert r.status_code == 201 and r.json()["construction"] == "Гексакоптер" and r.json()["link_type"] is None
    assert len(a.get("/api/v1/catalog/drone-models").json()["items"]) == 1
    # Удаление типа не меняет заведённые дроны.
    assert a.delete(f"/api/v1/catalog/drone-models/{m['id']}").status_code == 204
    dd = t.get(f"/api/v1/drones/{d['id']}").json()
    assert dd["model"] == "Курьер-2" and dd["drone_model_id"] is None
    # Параметры дрона видны в карточке заказа.
    o = world.create_order()
    assert "link_type" in o["drone"] and o["drone"]["registration_number"]


def test_drone_archive_and_report(world, api):
    t = world.technician
    did = world.drone_id
    o = world.placed_order()
    r = t.post(f"/api/v1/drones/{did}/archive", json={"reason": "broken"})
    assert r.status_code == 409 and str(o["number"]) in r.json()["error"]["message"]      # незавершённый заказ
    world.customer.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "передумал"})
    assert t.post(f"/api/v1/drones/{did}/archive", json={"reason": "other"}).status_code == 422   # нужен комментарий
    assert api("technician2").post(f"/api/v1/drones/{did}/archive", json={"reason": "lost"}).status_code == 404
    r = t.post(f"/api/v1/drones/{did}/archive", json={"reason": "other", "comment": "Сгорел регулятор"})
    assert r.status_code == 200, r.text
    assert r.json()["archived"] and not r.json()["published"]
    # Пропал из «Мой парк» и из выбора заказчика, появился в архиве.
    assert t.get(f"/api/v1/fleets/{world.fleet_id}/drones").json()["items"] == []
    assert t.get("/api/v1/fleets").json()["items"][0]["drone_count"] == 0
    assert did not in [x["id"] for x in world.customer.get("/api/v1/available-drones").json()["items"]]
    arch = t.get("/api/v1/drone-archive").json()["items"]
    assert [(x["id"], x["archive_reason"], x["archive_comment"]) for x in arch] == [(did, "other", "Сгорел регулятор")]
    r = t.post(f"/api/v1/drones/{did}/publish", json={"published": True})
    assert r.status_code == 409 and "архив" in r.json()["error"]["message"]
    # Отчёт: все дроны, архивные — в конце.
    r = t.post("/api/v1/drones", json={"serial_number": "SN-R2", "registration_number": "RU-R2", "manufacturer": "Maker",
                                    "model": "X", "max_payload_g": 500, "max_range_m": 3000, "fleet_id": world.fleet_id})
    assert r.status_code == 201, r.text
    rep = t.get("/api/v1/tech-report").json()
    assert [x["archived"] for x in rep["items"]] == [False, True]
    assert rep["technician"]["display_name"] and "subgroups" in rep["technician"]
    assert world.customer.get("/api/v1/tech-report").status_code == 403
    # Возврат из архива: снова в парке, требуется осмотр.
    r = t.delete(f"/api/v1/drones/{did}/archive")
    assert r.status_code == 200 and not r.json()["archived"] and r.json()["health_status"] == "requires_check"
    assert t.get("/api/v1/drone-archive").json()["items"] == []
    assert any(x["id"] == did for x in t.get(f"/api/v1/fleets/{world.fleet_id}/drones").json()["items"])


def test_profile_fields(world):
    a = world.admin
    co = a.post("/api/v1/org-units", json={"name": "ООО Дрон"}).json()
    dep = a.post("/api/v1/org-units", json={"name": "Техотдел", "parent_id": co["id"]}).json()
    shift = a.post("/api/v1/org-units", json={"name": "Смена 1", "parent_id": dep["id"]}).json()
    uid = world.technician.user["id"]
    r = a.patch(f"/api/v1/people/{uid}/profile", json={"org_unit_id": shift["id"], "position_title": "Старший техник",
                                                       "nickname": "Винт"})
    assert r.status_code == 200, r.text
    r = world.technician.put("/api/v1/me/profile", json={"display_name": "Техник Т.", "location": "Площадка №2"})
    assert r.status_code == 200
    p = world.technician.get("/api/v1/auth/me").json()["profile"]
    assert p["group"] == "ООО Дрон" and p["subgroups"] == ["Техотдел", "Смена 1"]
    assert p["position_title"] == "Старший техник" and p["location"] == "Площадка №2"
    assert p["nickname"] is None          # никнейм не передан — очищается пользователем
    assert world.technician.get("/api/v1/tech-report").json()["technician"]["group"] == "ООО Дрон"
