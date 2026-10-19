"""Внутренние группы, учётные записи приложения, бан и приостановка."""
from __future__ import annotations

from conftest import Api

PASSWORD = "Novikov-2026-pass"


def _new(client, **extra):
    body = {"username": "i.petrov", "password": PASSWORD, "last_name": "Петров", "first_name": "Иван",
            "position_title": "Диспетчер", "roles": ["customer"], **extra}
    return client.post("/api/v1/people", json=body)


def test_org_unit_tree(world, api):
    rec = api("recruiter")
    a = world.admin
    co = a.post("/api/v1/org-units", json={"name": "ООО «Ромашка»"}).json()
    dep = a.post("/api/v1/org-units", json={"name": "Логистика", "parent_id": co["id"]}).json()
    room = a.post("/api/v1/org-units", json={"name": "Кабинет 305", "parent_id": dep["id"]}).json()
    sub = a.post("/api/v1/org-units", json={"name": "Стол 2", "parent_id": room["id"]}).json()     # любая глубина
    assert a.post("/api/v1/org-units", json={"name": "логистика", "parent_id": co["id"]}).status_code == 409
    items = {u["name"]: u for u in rec.get("/api/v1/org-units").json()["items"]}
    assert items["Стол 2"]["path"] == "ООО «Ромашка» / Логистика / Кабинет 305 / Стол 2"
    assert a.patch(f"/api/v1/org-units/{dep['id']}", json={"name": "Отдел логистики"}).status_code == 200
    assert a.patch(f"/api/v1/org-units/{co['id']}", json={"parent_id": sub["id"], "move": True}).status_code == 422
    assert rec.post("/api/v1/org-units", json={"name": "X"}).status_code == 403          # дерево — администратор
    # пользователь в подгруппе находится и по родительской группе
    u = _new(rec, org_unit_id=room["id"]).json()
    assert u["org_unit"]["path"].endswith("Кабинет 305")
    found = rec.get(f"/api/v1/people?org_unit={co['id']}").json()["items"]
    assert [x["username"] for x in found] == ["i.petrov"]
    assert a.delete(f"/api/v1/org-units/{room['id']}").status_code == 409                 # есть подгруппа и люди
    assert a.delete(f"/api/v1/org-units/{sub['id']}").status_code == 204


def test_recruiter_creates_local_account_and_login(world, app, api):
    rec = api("recruiter")
    assert _new(rec, password="short1").status_code == 422
    assert _new(rec, roles=["admin"]).status_code == 403                                  # админа — только админ
    r = _new(rec, roles=["customer", "recruiter"])
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["auth_source"] == "local" and card["registered_by"]["username"] == "recruiter"
    assert card["password_changed_by"]["username"] == "recruiter" and card["status"] == "active"
    assert _new(rec).status_code == 409                                                   # логин занят
    me = Api(app, "i.petrov")
    assert me.login(password="wrong-password-1").status_code == 401
    r = me.login(password=PASSWORD)
    assert r.status_code == 200 and "order.read_own" not in r.json()["user"]["permissions"]   # профиль не проверен
    assert card["profile_status"] == "pending" and card["profile_verified"] is False
    assert rec.post(f"/api/v1/recruitment/users/{card['id']}/profile", json={"approve": True}).status_code == 200
    assert "order.read_own" in me.get("/api/v1/auth/me").json()["permissions"]
    hist = [h["action"] for h in rec.get(f"/api/v1/people/{card['id']}/history").json()["items"]]
    assert hist[0] == "user.created_local" and "user.profile_reviewed" in hist
    assert world.customer.get(f"/api/v1/people/{card['id']}/history").status_code == 403
    # рекрутер не меняет анкету и пароль, администратор — меняет с записью кто и когда
    assert rec.patch(f"/api/v1/people/{card['id']}/profile", json={"position_title": "X"}).status_code == 403
    assert rec.patch(f"/api/v1/people/{card['id']}/profile", json={"org_unit_id": None}).status_code == 200
    assert rec.post(f"/api/v1/people/{card['id']}/credentials", json={"password": "Another-pass-77"}).status_code == 403
    c = world.admin.post(f"/api/v1/people/{card['id']}/credentials",
                         json={"username": "ivan.petrov", "password": "Another-pass-77"}).json()
    assert c["username"] == "ivan.petrov" and c["password_changed_by"]["username"] == "admin"
    assert me.get("/api/v1/auth/me").status_code == 401                                   # сессии отозваны
    assert Api(app, "ivan.petrov").login(password="Another-pass-77").status_code == 200
    p = world.admin.patch(f"/api/v1/people/{card['id']}/profile", json={"middle_name": "Сергеевич"}).json()
    assert p["display_name"] == "Петров Иван Сергеевич"
    # администратор выдаёт роль администратора — в карточке видно кто и когда
    world.admin.post(f"/api/v1/recruitment/users/{card['id']}/roles", json={"role": "admin", "grant": True})
    adm = next(r for r in world.admin.get(f"/api/v1/people/{card['id']}").json()["roles"] if r["role"] == "admin")
    assert adm["granted_by"]["username"] == "admin" and adm["granted_at"]
    assert world.admin.post(f"/api/v1/people/{world.customer.user['id']}/credentials",
                            json={"password": "Domain-pass-11"}).status_code == 409           # доменная запись


def test_ban_and_suspend(world, app):
    cid = world.customer.user["id"]
    url = f"/api/v1/people/{cid}/status"
    assert world.admin.post(url, json={"status": "suspended"}).status_code == 422          # нужна причина
    r = world.admin.post(url, json={"status": "suspended", "reason": "Проверка документов"})
    assert r.status_code == 200 and r.json()["status_label"] == "приостановлена"
    me = world.customer.get("/api/v1/auth/me").json()                                   # вход сохраняется
    assert me["account_status"] == "suspended" and "order.create" not in me["permissions"]
    assert world.customer.get("/api/v1/orders?as=customer").status_code == 403
    assert world.customer.post("/api/v1/orders", json=world.order_payload()).status_code == 403
    r = world.admin.post(url, json={"status": "banned", "reason": "Нарушение правил"})
    assert r.status_code == 200
    assert world.customer.get("/api/v1/auth/me").status_code == 401                     # сессии отозваны
    r = Api(app, "customer").login()
    assert r.status_code == 401 and r.json()["error"]["message"] == "Ваш аккаунт забанен. Обратитесь в техподдержку"
    lst = {u["username"]: u for u in world.admin.get("/api/v1/people?status=banned").json()["items"]}
    assert lst["customer"]["status_label"] == "забанена"
    world.admin.post(url, json={"status": "active"})
    again = Api(app, "customer")
    assert again.login().status_code == 200
    assert again.post(f"/api/v1/people/{world.admin.user['id']}/status",
                               json={"status": "banned", "reason": "xxx"}).status_code == 403
    audit = world.admin.get("/api/v1/admin/audit?action=user.banned").json()["items"]
    assert audit and audit[0]["details"]["reason"] == "Нарушение правил"
