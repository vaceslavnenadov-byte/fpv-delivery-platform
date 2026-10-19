"""Общие фикстуры интеграционных тестов backend.

Тесты выполняются на реальном PostgreSQL (13+). Для каждого теста создаётся
чистая база из шаблона с применёнными миграциями.

Переменные окружения:
  FPV_TEST_PG — базовый URL сервера без имени БД,
                по умолчанию postgresql+psycopg://fpv:fpv@127.0.0.1:5432
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fpv import clock, db as dbmod, runtime  # noqa: E402
from fpv.config import NoFlyZone, Settings  # noqa: E402
from fpv.migrate import migrate  # noqa: E402
from fpv.services import events  # noqa: E402
from fpv.services.directory import StaticDirectoryProvider, pbkdf2_hash  # noqa: E402
from fpv.services.storage import FilesystemObjectStore  # noqa: E402

PG = os.environ.get("FPV_TEST_PG", "postgresql+psycopg://fpv:fpv@127.0.0.1:5432")
TEMPLATE = "fpv_test_template"
DB_NAME = "fpv_test_run"
GROUP = "CN={},OU=FPV,OU=Groups,DC=fpv,DC=local"
PASSWORD = "Str0ng-Domain-Pa$$"

USERS = {
    "customer": ["FPV-Customers"],
    "customer2": ["FPV-Customers"],
    "operator": ["FPV-Operators"],
    "operator2": ["FPV-Operators"],
    "coordinator": ["FPV-Coordinators"],
    "coordinator2": ["FPV-Coordinators"],
    "coordinator3": ["FPV-Coordinators"],
    "technician": ["FPV-Technicians"],
    "technician2": ["FPV-Technicians"],
    "recruiter": ["FPV-Recruiters"],
    "newbie": [],
    "admin": ["FPV-Admins"],
    "norole": ["Domain Users"],
}


def _admin_engine():
    return create_engine(f"{PG}/postgres", isolation_level="AUTOCOMMIT", future=True)


@pytest.fixture(scope="session")
def template_db():
    eng = _admin_engine()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE}"'))
        c.execute(text(f'CREATE DATABASE "{TEMPLATE}"'))
    tpl = create_engine(f"{PG}/{TEMPLATE}", future=True)
    migrate(tpl, ROOT / "database" / "migrations")
    tpl.dispose()
    yield TEMPLATE
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE}"'))
    eng.dispose()


@pytest.fixture()
def directory_file(tmp_path):
    path = tmp_path / "directory.json"
    users = []
    for i, (name, groups) in enumerate(USERS.items()):
        users.append({
            "username": name, "object_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
            "display_name": name.capitalize(), "email": f"{name}@fpv.local",
            "password_pbkdf2": pbkdf2_hash(PASSWORD, iterations=1000),
            "groups": [GROUP.format(g) for g in groups], "enabled": True,
        })
    path.write_text(json.dumps({"available": True, "users": users}), encoding="utf-8")
    return path


class Directory:
    """Управление эмулятором каталога из тестов (блокировка, группы, недоступность)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _edit(self, func):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        func(data)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def set_enabled(self, username: str, enabled: bool) -> None:
        def f(d):
            for u in d["users"]:
                if u["username"] == username:
                    u["enabled"] = enabled
        self._edit(f)

    def set_groups(self, username: str, groups) -> None:
        def f(d):
            for u in d["users"]:
                if u["username"] == username:
                    u["groups"] = [GROUP.format(g) for g in groups]
        self._edit(f)

    def remove(self, username: str) -> None:
        self._edit(lambda d: d.update(users=[u for u in d["users"] if u["username"] != username]))

    def set_available(self, available: bool) -> None:
        self._edit(lambda d: d.update(available=available))


@pytest.fixture()
def settings(tmp_path) -> Settings:
    s = Settings(
        environment="test",
        database_url=f"{PG}/{DB_NAME}",
        directory_provider="static",
        cookie_secure=False,
        login_rate_per_min=1000,
        mutation_rate_per_min=10000,
        directory_revalidate_s=300,
        session_idle_s=4 * 3600,
        video_spool_dir=str(tmp_path / "spool"),
        object_store_root=str(tmp_path / "objects"),
        service_area=[55.55, 37.35, 55.95, 37.90],
        no_fly_zones=[NoFlyZone("Тестовая зона", 55.60, 37.40, 3000.0)],
    )
    return s


@pytest.fixture()
def app(template_db, settings, directory_file):
    eng = _admin_engine()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
        c.execute(text(f'CREATE DATABASE "{DB_NAME}" TEMPLATE "{template_db}"'))
    settings.static_directory_file = str(directory_file)
    from fpv.main import create_app
    application = create_app(settings, directory=StaticDirectoryProvider(str(directory_file)),
                             store=FilesystemObjectStore(settings.object_store_root))
    yield application
    clock.set_override(None)
    dbmod.get_engine().dispose()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'))
    eng.dispose()


@pytest.fixture()
def directory(directory_file) -> Directory:
    return Directory(directory_file)


class Api:
    """Клиент API от имени доменного пользователя (cookie-сессия + CSRF)."""

    def __init__(self, app, username: Optional[str] = None) -> None:
        from fastapi.testclient import TestClient
        self.client = TestClient(app, base_url="http://testserver")
        self.username = username
        self.csrf = ""
        self.user: Dict = {}

    def login(self, username: Optional[str] = None, password: str = PASSWORD, **extra):
        username = username or self.username
        r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password, **extra})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
            self.user = r.json()["user"]
        return r

    def _headers(self, headers=None, idem: Optional[str] = None):
        h = {"X-CSRF-Token": self.csrf}
        if idem:
            h["Idempotency-Key"] = idem
        h.update(headers or {})
        return h

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, json=None, idem=None, headers=None, **kw):
        return self.client.post(url, json=json, headers=self._headers(headers, idem), **kw)

    def put(self, url, json=None, **kw):
        return self.client.put(url, json=json, headers=self._headers(), **kw)

    def patch(self, url, json=None, **kw):
        return self.client.patch(url, json=json, headers=self._headers(), **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self._headers(), **kw)


@pytest.fixture()
def api(app):
    def make(username: str, login: bool = True) -> Api:
        a = Api(app, username)
        if login:
            r = a.login()
            assert r.status_code == 200, r.text
        return a
    return make


class DroneClient:
    """Сервисный модуль дрона (контур телеметрии)."""

    def __init__(self, app, key: str) -> None:
        from fastapi.testclient import TestClient
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Drone {key}"}
        self.seq = 0

    def telemetry(self, n: int = 1, battery: int = 90, **fields):
        records = []
        for _ in range(n):
            self.seq += 1
            records.append({"seq": self.seq, "recorded_at": clock.now().isoformat(), "lat": 55.75, "lon": 37.6,
                            "alt_m": 50.0, "battery_pct": battery, "link_quality": 95, "gnss_fix": True,
                            "flight_phase": "cruise", **fields})
        return self.client.post("/drone-link/v1/telemetry", json={"records": records}, headers=self.headers)

    def segment(self, seq: int, data: bytes = None, duration_ms: int = 1000):
        data = data if data is not None else (b"TS%06d" % seq) * 100
        return self.client.post("/drone-link/v1/video/segments", content=data,
                                headers={**self.headers, "X-Segment-Seq": str(seq),
                                         "X-Segment-Duration-Ms": str(duration_ms),
                                         "Content-Type": "video/mp2t"})

    def event(self, kind: str, **data):
        return self.client.post("/drone-link/v1/events", json={"kind": kind, "data": data}, headers=self.headers)


class FakeClock:
    def __init__(self) -> None:
        self.current = dt.datetime.now(dt.timezone.utc)
        clock.set_override(lambda: self.current)

    def advance(self, **kw) -> None:
        self.current = self.current + dt.timedelta(**kw)


@pytest.fixture()
def fake_clock(app):
    c = FakeClock()
    yield c
    clock.set_override(None)


def run_outbox():
    total = {"done": 0, "retry": 0, "dead": 0}
    for _ in range(10):
        stats = events.process_batch(dbmod.new_session)
        for k, v in stats.items():
            total[k] += v
        if not any(stats.values()):
            break
    return total


# ---------------------------------------------------------------------------
# Сценарные помощники
# ---------------------------------------------------------------------------

ORIGIN = {"lat": 55.7500, "lon": 37.6000, "address": "Склад, ул. Примерная, 1"}
DEST = {"lat": 55.7600, "lon": 37.6300, "address": "Офис, пр. Тестовый, 10"}
BASE = {"lat": 55.7450, "lon": 37.5900, "address": "База парка, ул. Ангарная, 3"}


@pytest.fixture()
def world(app, api, directory):
    """Готовое окружение: пользователи всех ролей, подтверждённый оператор,
    допущенный дрон с ключом и подключённым сервисным модулем."""
    return World(app, api)


class World:
    def __init__(self, app, api) -> None:
        self.app = app
        self._api = api
        self.customer = api("customer")
        self.admin = api("admin")
        self.operator = api("operator")
        self.technician = api("technician")
        self.coordinator = api("coordinator")
        r = self.operator.post("/api/v1/me/operator-application", json={"license_number": "OP-0001"})
        assert r.status_code == 201, r.text
        r = self.admin.post(f"/api/v1/admin/operators/{self.operator.user['id']}/decision",
                            json={"approve": True, "notes": "Допуск подтверждён"})
        assert r.status_code == 200, r.text
        r = self.technician.post("/api/v1/fleets", json={"name": "Парк «Центр»", "base": BASE})
        assert r.status_code == 201, r.text
        self.fleet_id = r.json()["id"]
        self.drone_id, self.drone_key = self.make_drone(self.technician, "SN-0001", "RU-0001")
        self.drone = DroneClient(app, self.drone_key)
        assert self.drone.telemetry().status_code == 200

    def make_drone(self, owner: Api, serial: str, reg: str, serviceable: bool = True, publish: bool = True):
        """Дрон парка техника: регистрация → подтверждение администратором → осмотр → ключ → открыт для заказов."""
        body = {"serial_number": serial, "registration_number": reg, "manufacturer": "Гражданские БВС",
                "model": "Курьер-1", "max_payload_g": 1500, "max_range_m": 20000}
        if owner is self.technician:
            body["fleet_id"] = self.fleet_id
        r = owner.post("/api/v1/drones", json=body)
        assert r.status_code == 201, r.text
        drone_id = r.json()["id"]
        r = self.admin.post(f"/api/v1/drones/{drone_id}/decision", json={"action": "approve"})
        assert r.status_code == 200, r.text
        if serviceable:
            r = self.technician.post(f"/api/v1/drones/{drone_id}/inspections",
                                     json={"result": "pass", "set_status": "serviceable", "battery_pct": 95,
                                           "comment": "Осмотр пройден"})
            assert r.status_code == 201, r.text
        r = owner.post(f"/api/v1/drones/{drone_id}/key")
        assert r.status_code == 200, r.text
        key = r.json()["api_key"]
        if serviceable and publish and owner is self.technician:
            r = self.technician.post(f"/api/v1/drones/{drone_id}/publish", json={"published": True})
            assert r.status_code == 200, r.text
        return drone_id, key

    def tech_confirm(self, order_id: str, drone: "DroneClient" = None, drone_id: str = None) -> Dict:
        """Техник парка: проверка видео (сегмент пробной трансляции) и подтверждение готовности к заказу."""
        drone, drone_id = drone or self.drone, drone_id or self.drone_id
        r = self.technician.post(f"/api/v1/drones/{drone_id}/video-check")
        assert r.status_code == 200, r.text
        assert drone.telemetry().status_code == 200
        r = drone.segment(900000 + drone.seq)
        assert r.status_code == 200 and r.json().get("check"), r.text
        r = self.technician.post(f"/api/v1/orders/{order_id}/tech-ready")
        assert r.status_code == 200 and r.json()["ready"], r.text
        return r.json()["order"]

    def order_payload(self, **overrides):
        now = clock.now()
        body = {"drone_id": self.drone_id, "cargo_type": "documents", "cargo_description": "Пакет документов",
                "cargo_weight_g": 300, "cargo_length_mm": 300, "cargo_width_mm": 220, "cargo_height_mm": 20,
                "origin": ORIGIN, "destination": DEST, "recipient_name": "Иван Получатель",
                "recipient_phone": "+7 900 000-00-00",
                "window_start": (now + dt.timedelta(minutes=30)).isoformat(),
                "window_end": (now + dt.timedelta(minutes=90)).isoformat()}
        body.update(overrides)
        return body

    def create_order(self, **overrides) -> Dict:
        r = self.customer.post("/api/v1/orders", json=self.order_payload(**overrides))
        assert r.status_code == 201, r.text
        return r.json()

    def placed_order(self) -> Dict:
        """Заказ в очереди операторов."""
        o = self.create_order()
        r = self.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
        assert r.status_code == 200 and r.json()["status"] == "pending_confirmation", r.text
        r = self.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={})
        assert r.status_code == 200 and r.json()["status"] == "awaiting_coordinator", r.text
        return self.coordinator_approve(o["id"])

    def coordinator_approve(self, order_id: str) -> Dict:
        """Назначенный координатор согласует размещённый заказ — он уходит в очередь операторов."""
        assigned = self.admin.get(f"/api/v1/orders/{order_id}").json()["coordinator"]
        who = self.coordinator if not assigned or assigned["username"] == "coordinator" else self._api(assigned["username"])
        r = who.post(f"/api/v1/orders/{order_id}/coordinator-decision", json={"approve": True})
        assert r.status_code == 200 and r.json()["status"] == "awaiting_operator", r.text
        return r.json()

    def ready_order(self) -> Dict:
        o = self.placed_order()
        r = self.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
        assert r.status_code == 200, r.text
        self.tech_confirm(o["id"])
        r = self.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
        assert r.status_code == 200 and r.json()["ready"], r.text
        return r.json()["order"]

    def started_order(self) -> Dict:
        o = self.ready_order()
        r = self.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
        assert r.status_code == 200 and r.json()["status"] == "in_progress", r.text
        return r.json()

    def status(self, order_id: str) -> str:
        return self.admin.get(f"/api/v1/orders/{order_id}").json()["status"]
