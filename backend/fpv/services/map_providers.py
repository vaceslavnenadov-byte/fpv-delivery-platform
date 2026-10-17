"""Онлайн-карты по API-ключу (Map Service).

Администратор подключает плиточный сервис карт — ESRI (ArcGIS), Google (Map Tiles API), Яндекс
(Tiles API), 2ГИС, OpenStreetMap или любой XYZ-сервис по шаблону адреса — и вводит API-ключ.
Браузеры получают плитки только через сервер платформы:

* ключ не покидает сервер (в API отдаётся только маска);
* политика безопасности сайта не меняется (CSP: img-src 'self');
* доступ к плиткам — по разрешению map.read, с ограничением числа плиток в минуту на пользователя,
  чтобы ключ нельзя было использовать для массовой выгрузки карт.

Плитки хранятся только в памяти процесса до 10 минут — условия большинства сервисов не разрешают
долговременное хранение. Соблюдение условий использования конкретного сервиса (лицензия, лимиты,
атрибуция) — ответственность организации-владельца ключа.

Адрес сервиса задаёт администратор, но запросы выполняет сервер, поэтому адрес проверяется:
только https, без перенаправлений, без адресов внутренней сети (если не разрешено настройкой
FPV_MAP_TILE_ALLOW_PRIVATE), ответ — только изображение PNG, JPEG или WebP.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__, clock
from ..config import Settings
from ..errors import AppError, NotFound, RateLimited, ValidationFailed
from ..models import MapProvider, Order
from . import audit
from .identity import Principal
from .maps import sniff_image

GOOGLE_SESSION_URL = "https://tile.googleapis.com/v1/createSession?key={key}"
GOOGLE_TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session={session}&key={key}"
MAX_TILE_BYTES = 2 * 1024 * 1024
CACHE_TTL_S = 600

# Готовые настройки. Шаблон: {z} {x} {y} — плитка, {key} — API-ключ, {s} — поддомен.
PRESETS: Dict[str, Dict[str, Any]] = {
    "esri_imagery": {
        "title": "ESRI — спутник (World Imagery)", "key_required": True, "projection": "3857", "max_zoom": 19,
        "url_template": "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token={key}",
        "attribution": "Esri, Maxar, Earthstar Geographics", "help": "Ключ ArcGIS Location Platform (developers.arcgis.com) с доступом к Basemap styles."},
    "esri_streets": {
        "title": "ESRI — улицы (World Street Map)", "key_required": True, "projection": "3857", "max_zoom": 19,
        "url_template": "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}?token={key}",
        "attribution": "Esri, HERE, Garmin, OpenStreetMap contributors", "help": "Ключ ArcGIS Location Platform."},
    "esri_topo": {
        "title": "ESRI — топографическая (World Topo Map)", "key_required": True, "projection": "3857", "max_zoom": 19,
        "url_template": "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}?token={key}",
        "attribution": "Esri, HERE, Garmin, OpenStreetMap contributors", "help": "Ключ ArcGIS Location Platform."},
    "google_roadmap": {
        "title": "Google — схема", "key_required": True, "projection": "3857", "max_zoom": 21, "google_map_type": "roadmap",
        "url_template": "", "attribution": "© Google",
        "help": "Ключ Google Cloud с включённым Map Tiles API; сеанс плиток создаётся автоматически."},
    "google_satellite": {
        "title": "Google — спутник", "key_required": True, "projection": "3857", "max_zoom": 21, "google_map_type": "satellite",
        "url_template": "", "attribution": "© Google", "help": "Ключ Google Cloud с включённым Map Tiles API."},
    "yandex_map": {
        "title": "Яндекс — схема", "key_required": True, "projection": "3395", "max_zoom": 21,
        "url_template": "https://tiles.api-maps.yandex.ru/v1/tiles/?x={x}&y={y}&z={z}&lang=ru_RU&l=map&apikey={key}",
        "attribution": "© Яндекс", "help": "Ключ «Tiles API» из кабинета разработчика Яндекса. Проекция Яндекса — EPSG:3395."},
    "dgis": {
        "title": "2ГИС — схема", "key_required": False, "projection": "3857", "max_zoom": 18,
        "url_template": "https://tile{s}.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1",
        "subdomains": "0123", "attribution": "© 2ГИС",
        "help": "Растровые плитки 2ГИС. Если по договору с 2ГИС выдан ключ и другой адрес плиток — укажите их в шаблоне."},
    "osm": {
        "title": "OpenStreetMap", "key_required": False, "projection": "3857", "max_zoom": 19,
        "url_template": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "subdomains": "",
        "attribution": "© OpenStreetMap contributors",
        "help": "Без ключа, для небольшой нагрузки (правила tile.openstreetmap.org). Для рабочей нагрузки — свой сервер плиток."},
    "custom": {
        "title": "Свой сервис (XYZ по шаблону)", "key_required": False, "projection": "3857", "max_zoom": 19,
        "url_template": "", "attribution": "",
        "help": "Любой сервис плиток XYZ (MapTiler, Mapbox Raster, HERE, Thunderforest, свой сервер): https-адрес с {z}, {x}, {y}, при необходимости {key} и {s}."},
}


class TileError(AppError):
    status_code = 502
    code = "tile_unavailable"


# ---------------------------------------------------------------------------
# Хранение и проверка настроек
# ---------------------------------------------------------------------------

def _mask(key: str) -> str:
    return "" if not key else ("•" * 6 + key[-4:] if len(key) > 6 else "•" * len(key))


def serialize(m: MapProvider, *, admin: bool = False) -> Dict[str, Any]:
    data = {"id": str(m.id), "preset": m.preset, "name": m.name, "projection": m.projection,
            "min_zoom": m.min_zoom, "max_zoom": m.max_zoom, "attribution": m.attribution,
            "tile_url": f"/api/v1/map-providers/{m.id}/tiles/{{z}}/{{x}}/{{y}}?v={int(m.updated_at.timestamp()) if m.updated_at else 0}"}
    if admin:
        data.update({"active": m.active, "sort_order": m.sort_order, "url_template": m.url_template,
                     "api_key_masked": _mask(m.api_key), "has_key": bool(m.api_key),
                     "last_error": m.last_error, "last_error_at": m.last_error_at.isoformat() if m.last_error_at else None})
    return data


def list_providers(db: Session, p: Principal, *, include_inactive: bool = False) -> List[MapProvider]:
    p.require("map.read")
    stmt = select(MapProvider).order_by(MapProvider.sort_order, MapProvider.name)
    if not (include_inactive and p.has("map.manage")):
        stmt = stmt.where(MapProvider.active.is_(True))
    return list(db.execute(stmt).scalars())


def get_active(db: Session, provider_id: uuid.UUID) -> MapProvider:
    m = db.get(MapProvider, provider_id)
    if m is None or not m.active:
        raise NotFound("Карта не найдена или отключена")
    return m


def _check_template(template: str, preset: str) -> None:
    if PRESETS[preset].get("google_map_type"):
        return
    if not template.startswith("https://"):
        raise ValidationFailed("Адрес плиток должен начинаться с https://", code="tile_template")
    for ph in ("{z}", "{x}", "{y}"):
        if ph not in template:
            raise ValidationFailed(f"В адресе плиток нет {ph}", code="tile_template")
    host = urllib.parse.urlsplit(template.replace("{s}", "a")).hostname or ""
    if not host or "{" in host.replace("{s}", ""):
        raise ValidationFailed("Некорректный адрес сервиса плиток", code="tile_template")


def _apply(m: MapProvider, data: Dict[str, Any]) -> None:
    preset = PRESETS[m.preset]
    for f in ("name", "attribution", "projection", "min_zoom", "max_zoom", "active", "sort_order"):
        if f in data and data[f] is not None:
            setattr(m, f, data[f])
    if data.get("url_template") is not None:
        m.url_template = data["url_template"].strip()
    if data.get("api_key") is not None:            # пустая строка — удалить ключ
        m.api_key = data["api_key"].strip()
    if m.preset != "custom" and not m.url_template and not preset.get("google_map_type"):
        m.url_template = preset["url_template"]
    _check_template(m.url_template, m.preset)
    if preset["key_required"] and not m.api_key:
        raise ValidationFailed(f"Для «{preset['title']}» нужен API-ключ", code="api_key_required")
    if "{key}" in m.url_template and not m.api_key:
        raise ValidationFailed("В шаблоне есть {key}, но ключ не указан", code="api_key_required")
    if m.min_zoom > m.max_zoom:
        raise ValidationFailed("Минимальный масштаб больше максимального")


def create(db: Session, p: Principal, data: Dict[str, Any]) -> MapProvider:
    p.require("map.manage")
    preset_code = data.get("preset") or "custom"
    if preset_code not in PRESETS:
        raise ValidationFailed("Неизвестный сервис карт")
    preset = PRESETS[preset_code]
    m = MapProvider(preset=preset_code, name=(data.get("name") or preset["title"]).strip(),
                    url_template="", api_key="", projection=preset["projection"], min_zoom=1,
                    max_zoom=preset["max_zoom"], attribution=preset["attribution"], active=True,
                    sort_order=100, created_by=p.user_id, created_at=clock.now(), updated_at=clock.now())
    _apply(m, {k: v for k, v in data.items() if k != "preset"})
    db.add(m)
    db.flush()
    audit.record(db, p.actor, "map_provider.created", entity_type="map_provider", entity_id=m.id,
                 details={"preset": m.preset, "name": m.name, "host": _host(m), "has_key": bool(m.api_key)})
    return m


def update(db: Session, p: Principal, provider_id: uuid.UUID, data: Dict[str, Any]) -> MapProvider:
    p.require("map.manage")
    m = db.get(MapProvider, provider_id)
    if m is None:
        raise NotFound("Карта не найдена")
    _apply(m, data)
    m.updated_at = clock.now()
    if "api_key" in data or "url_template" in data:
        m.last_error = m.last_error_at = None
        _google_sessions.pop(m.id, None)
    db.flush()
    audit.record(db, p.actor, "map_provider.updated", entity_type="map_provider", entity_id=m.id,
                 details={k: ("***" if k == "api_key" else v) for k, v in data.items()})
    return m


def delete(db: Session, p: Principal, provider_id: uuid.UUID) -> None:
    p.require("map.manage")
    m = db.get(MapProvider, provider_id)
    if m is None:
        raise NotFound("Карта не найдена")
    audit.record(db, p.actor, "map_provider.deleted", entity_type="map_provider", entity_id=m.id,
                 details={"name": m.name, "preset": m.preset})
    db.delete(m)            # у заказов ссылка обнуляется (ON DELETE SET NULL)
    _google_sessions.pop(provider_id, None)


def _host(m: MapProvider) -> str:
    if PRESETS[m.preset].get("google_map_type"):
        return "tile.googleapis.com"
    return urllib.parse.urlsplit(m.url_template.replace("{s}", "a")).hostname or ""


# ---------------------------------------------------------------------------
# Загрузка плиток
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - перенаправления запрещены
        return None


_opener = urllib.request.build_opener(_NoRedirect)
_cache: "OrderedDict[Tuple, Tuple[float, str, bytes]]" = OrderedDict()
_cache_lock = threading.Lock()
_rate: Dict[uuid.UUID, Tuple[int, int]] = {}
_rate_lock = threading.Lock()
_google_sessions: Dict[uuid.UUID, Tuple[str, float]] = {}


def _check_host(url: str, settings: Settings) -> None:
    host = urllib.parse.urlsplit(url).hostname or ""
    if settings.map_tile_allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise TileError(f"Сервер карт {host} не найден (DNS)") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise TileError(f"Адрес {host} во внутренней сети — запрещено настройкой FPV_MAP_TILE_ALLOW_PRIVATE")


def http_request(url: str, *, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
                 timeout: float = 8.0) -> Tuple[int, str, bytes]:
    """Запрос к сервису карт. Вынесен отдельно, чтобы в тестах подменять сеть."""
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": f"FPV-Delivery-Platform/{__version__} (map tiles proxy)", "Accept": "image/*,application/json",
        **(headers or {})}, method="POST" if data is not None else "GET")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_TILE_BYTES + 1)
            return resp.status, resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", exc.read(4096) if exc.fp else b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TileError(f"Сервис карт недоступен: {reason}") from exc


def _google_session(m: MapProvider, settings: Settings) -> str:
    cached = _google_sessions.get(m.id)
    if cached and cached[1] - 3600 > time.time():
        return cached[0]
    url = GOOGLE_SESSION_URL.format(key=urllib.parse.quote(m.api_key, safe=""))
    _check_host(url, settings)
    body = json.dumps({"mapType": PRESETS[m.preset]["google_map_type"], "language": "ru-RU", "region": "RU"}).encode()
    status, _, raw = http_request(url, data=body, headers={"Content-Type": "application/json"},
                                  timeout=settings.map_tile_timeout_s)
    if status != 200:
        raise TileError(f"Google отказал в сеансе плиток (HTTP {status}): {_short(raw)}")
    data = json.loads(raw)
    session, expiry = data["session"], float(data.get("expiry") or time.time() + 86400)
    _google_sessions[m.id] = (session, expiry)
    return session


def _short(raw: bytes) -> str:
    return raw[:200].decode("utf-8", "replace").replace("\n", " ")


def tile_url(m: MapProvider, z: int, x: int, y: int, settings: Settings) -> str:
    preset = PRESETS[m.preset]
    key = urllib.parse.quote(m.api_key, safe="")
    if preset.get("google_map_type"):
        return GOOGLE_TILE_URL.format(z=z, x=x, y=y, key=key, session=_google_session(m, settings))
    subs = preset.get("subdomains", "abc") or "a"
    return (m.url_template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            .replace("{key}", key).replace("{s}", subs[(x + y) % len(subs)]))


def _rate_check(user_id: uuid.UUID, settings: Settings) -> None:
    minute = int(time.time() // 60)
    with _rate_lock:
        cur_minute, count = _rate.get(user_id, (minute, 0))
        if cur_minute != minute:
            cur_minute, count = minute, 0
        if count >= settings.map_tile_rate_per_min:
            raise RateLimited("Слишком много запросов карты — подождите минуту")
        _rate[user_id] = (cur_minute, count + 1)


def _record_error(db: Session, m: MapProvider, message: str) -> None:
    if m.last_error != message:
        m.last_error, m.last_error_at = message[:500], clock.now()
        db.commit()


def fetch_tile(db: Session, settings: Settings, p: Principal, provider_id: uuid.UUID,
               z: int, x: int, y: int) -> Tuple[str, bytes]:
    p.require("map.read")
    m = get_active(db, provider_id)
    if not (m.min_zoom <= z <= m.max_zoom) or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise NotFound("Плитка вне диапазона")
    return _fetch(db, settings, m, p.user_id, z, x, y)


def _fetch(db: Session, settings: Settings, m: MapProvider, user_id: uuid.UUID,
           z: int, x: int, y: int) -> Tuple[str, bytes]:
    key = (m.id, m.updated_at, z, x, y)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(key)
            return hit[1], hit[2]
    _rate_check(user_id, settings)
    try:
        url = tile_url(m, z, x, y, settings)
        _check_host(url, settings)
        status, ctype, body = http_request(url, timeout=settings.map_tile_timeout_s)
        if status == 404:
            raise NotFound("Плитки нет у сервиса карт")
        if status in (401, 403):
            _google_sessions.pop(m.id, None)
            raise TileError(f"Сервис карт отклонил ключ (HTTP {status}): {_short(body)}")
        if status != 200:
            raise TileError(f"Сервис карт ответил HTTP {status}: {_short(body)}")
        if len(body) > MAX_TILE_BYTES:
            raise TileError("Слишком большая плитка")
        try:
            kind, _, _ = sniff_image(body)
        except ValidationFailed as exc:
            raise TileError(f"Сервис карт вернул не изображение ({ctype or 'без типа'})") from exc
    except TileError as exc:
        _record_error(db, m, exc.message)
        raise
    if m.last_error:
        m.last_error = m.last_error_at = None
        db.commit()
    with _cache_lock:
        _cache[key] = (now, kind, body)
        while len(_cache) > settings.map_tile_cache_items:
            _cache.popitem(last=False)
    return kind, body


def test_provider(db: Session, settings: Settings, p: Principal, provider_id: uuid.UUID) -> Dict[str, Any]:
    """Проверка подключения: одна плитка в центре зоны обслуживания."""
    p.require("map.manage")
    m = db.get(MapProvider, provider_id)
    if m is None:
        raise NotFound("Карта не найдена")
    from . import zones
    lat_c, lon_c = zones.center(db, settings)
    z = max(m.min_zoom, min(12, m.max_zoom))
    x, y = lonlat_to_tile(lat_c, lon_c, z, m.projection)
    try:
        kind, body = _fetch(db, settings, m, p.user_id, z, x, y)
        return {"ok": True, "message": f"Плитка {z}/{x}/{y} получена ({kind}, {len(body)} байт)"}
    except AppError as exc:
        return {"ok": False, "message": exc.message}


def lonlat_to_tile(lat: float, lon: float, z: int, projection: str = "3857") -> Tuple[int, int]:
    import math
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    phi = math.radians(lat)
    if projection == "3395":
        e = 0.0818191908426
        merc = math.log(math.tan(math.pi / 4 + phi / 2) * ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))
    else:
        merc = math.log(math.tan(math.pi / 4 + phi / 2))
    y = int((1 - merc / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def order_provider(db: Session, order: Order) -> Optional[Dict[str, Any]]:
    if order.map_provider_id is None:
        return None
    m = db.get(MapProvider, order.map_provider_id)
    return {"id": str(m.id), "name": m.name, "active": m.active} if m else None
