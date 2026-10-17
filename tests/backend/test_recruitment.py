"""Роль «Рекрутер»: добавление пользователей, выдача ролей, проверка профилей и допуска операторов."""
from __future__ import annotations

from conftest import Api


def _find(client, username):
    items = client.get(f"/api/v1/recruitment/users?q={username}").json()["items"]
    return next(u for u in items if u["username"] == username)


def test_recruiter_registers_user_and_grants_role(world, app, api):
    rec = api("recruiter")
    # без роли войти нельзя
    assert Api(app, "newbie").login().status_code == 403
    r = rec.post("/api/v1/recruitment/users", json={"username": "newbie", "roles": ["customer"]})
    assert r.status_code == 201, r.text
    assert rec.post("/api/v1/recruitment/users", json={"username": "newbie"}).status_code == 409   # уже есть
    assert rec.post("/api/v1/recruitment/users", json={"username": "ghost"}).status_code == 404
    u = _find(rec, "newbie")
    assert {"role": "customer", "source": "manual"} in u["roles"]
    assert u["registered_by"] == rec.user["display_name"]
    newbie = api("newbie")                                   # теперь вход разрешён
    assert "order.read_own" in newbie.user["permissions"]
    # ещё одна роль и снятие
    assert rec.post(f"/api/v1/recruitment/users/{u['id']}/roles", json={"role": "technician", "grant": True}).status_code == 200
    assert rec.post(f"/api/v1/recruitment/users/{u['id']}/roles", json={"role": "technician", "grant": False}).status_code == 200
    audit = world.admin.get("/api/v1/admin/audit?action=user.registered").json()["items"]
    assert audit and audit[0]["details"]["username"] == "newbie"


def test_recruiter_cannot_grant_privileged_roles(world, api):
    rec = api("recruiter")
    cust = _find(rec, "customer")
    r = rec.post(f"/api/v1/recruitment/users/{cust['id']}/roles", json={"role": "admin", "grant": True})
    assert r.status_code == 403
    me = _find(rec, "recruiter")
    assert rec.post(f"/api/v1/recruitment/users/{me['id']}/roles",
                    json={"role": "operator", "grant": True}).status_code == 403
    # роль из доменной группы рекрутер не снимает
    r = rec.post(f"/api/v1/recruitment/users/{cust['id']}/roles", json={"role": "customer", "grant": False})
    assert r.status_code == 409
    # прочие пользователи раздела не видят; в админские разделы рекрутер не попадает
    assert world.customer.get("/api/v1/recruitment/users").status_code == 403
    assert rec.get("/api/v1/admin/users").status_code == 403
    codes = [x["code"] for x in rec.get("/api/v1/recruitment/roles").json()["items"]]
    assert codes == ["customer", "operator", "coordinator", "technician", "recruiter"]
    # администратор сохраняет все права и может назначить рекрутера
    assert world.admin.post(f"/api/v1/recruitment/users/{cust['id']}/roles",
                            json={"role": "recruiter", "grant": True}).status_code == 200
    assert "admin" in [x["code"] for x in world.admin.get("/api/v1/recruitment/roles").json()["items"]]


def test_profile_review(world, api):
    rec = api("recruiter")
    r = world.customer.put("/api/v1/me/profile", json={"display_name": "Анна Новая", "phone": "+7 900 111-22-33"})
    assert r.status_code == 200
    assert any("Профиль на проверку" in n["title"] for n in rec.get("/api/v1/me/notifications").json()["items"])
    pending = rec.get("/api/v1/recruitment/users?profile_status=pending").json()["items"]
    cust = next(u for u in pending if u["username"] == "customer")
    url = f"/api/v1/recruitment/users/{cust['id']}/profile"
    assert rec.post(url, json={"approve": False, "notes": ""}).status_code == 422
    assert rec.post(url, json={"approve": False, "notes": "Укажите полное ФИО"}).status_code == 200
    me = world.customer.get("/api/v1/auth/me").json()
    assert me["profile_status"] == "rejected" and me["profile_review_notes"] == "Укажите полное ФИО"
    world.customer.put("/api/v1/me/profile", json={"display_name": "Анна Петровна Заказчикова"})
    assert world.customer.get("/api/v1/auth/me").json()["profile_status"] == "pending"
    assert rec.post(url, json={"approve": True}).status_code == 200
    assert world.customer.get("/api/v1/auth/me").json()["profile_status"] == "verified"
    # собственный профиль рекрутер не подтверждает
    rec.put("/api/v1/me/profile", json={"display_name": "Римма Рекрутова"})
    me = _find(rec, "recruiter")
    assert rec.post(f"/api/v1/recruitment/users/{me['id']}/profile", json={"approve": True}).status_code == 403


def test_recruiter_approves_operator(world, api):
    rec = api("recruiter")
    op2 = api("operator2")
    assert op2.post("/api/v1/me/operator-application", json={"license_number": "OP-77"}).status_code == 201
    assert any("Заявка оператора" in n["title"] for n in rec.get("/api/v1/me/notifications").json()["items"])
    items = rec.get("/api/v1/admin/operators").json()["items"]
    assert any(o["username"] == "operator2" and o["status"] == "pending" for o in items)
    r = rec.post(f"/api/v1/admin/operators/{op2.user['id']}/decision", json={"approve": True, "notes": "Лицензия проверена"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
