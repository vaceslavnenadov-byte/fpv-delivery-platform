"""Конфигурация приложения.

Все параметры читаются из переменных окружения с префиксом ``FPV_``.
Секреты (пароль служебной учётной записи каталога, ключи S3, строка
подключения к БД) можно передавать через файл: если задана переменная
``FPV_<ИМЯ>_FILE``, значение читается из указанного файла. Так секреты не
попадают в окружение процессов, командную строку и журналы.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ConfigError(RuntimeError):
    pass


def _raw(name: str) -> Optional[str]:
    file_var = os.environ.get(f"FPV_{name}_FILE")
    if file_var:
        try:
            return Path(file_var).read_text(encoding="utf-8").strip()
        except OSError as exc:  # pragma: no cover - зависит от окружения
            raise ConfigError(f"Не удалось прочитать FPV_{name}_FILE: {exc.strerror}") from exc
    return os.environ.get(f"FPV_{name}")


def _str(name: str, default: str = "") -> str:
    value = _raw(name)
    return default if value is None or value == "" else value


def _int(name: str, default: int) -> int:
    value = _raw(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"FPV_{name} должно быть целым числом") from exc


def _float(name: str, default: float) -> float:
    value = _raw(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"FPV_{name} должно быть числом") from exc


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: List[str]) -> List[str]:
    value = _raw(name)
    if value is None or value.strip() == "":
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class NoFlyZone:
    name: str
    lat: float
    lon: float
    radius_m: float


@dataclass
class Settings:
    environment: str = "development"           # development | test | production
    database_url: str = "postgresql+psycopg://fpv:fpv@localhost:5432/fpv"

    # --- доменная аутентификация ---
    directory_provider: str = "static"         # ldap | static (static запрещён в production)
    static_directory_file: str = ""
    ldap_uri: str = "ldaps://dc1.fpv.local"
    ldap_start_tls: bool = False
    ldap_ca_file: str = ""
    ldap_base_dn: str = "DC=fpv,DC=local"
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_filter: str = "(&(objectClass=user)(sAMAccountName={username}))"
    ldap_attr_username: str = "sAMAccountName"
    ldap_attr_object_id: str = "objectGUID"
    ldap_attr_display_name: str = "displayName"
    ldap_attr_email: str = "mail"
    ldap_attr_upn: str = "userPrincipalName"
    ldap_attr_disabled: str = "userAccountControl"   # AD: бит 0x2; FreeIPA: nsAccountLock
    ldap_nested_groups: bool = True
    ldap_timeout_s: int = 5
    directory_revalidate_s: int = 300

    # --- сессии и защита ---
    session_ttl_s: int = 8 * 3600
    session_idle_s: int = 30 * 60
    cookie_secure: bool = True
    login_rate_per_min: int = 10
    mutation_rate_per_min: int = 240
    trusted_proxies: List[str] = field(default_factory=lambda: ["127.0.0.1"])

    # --- бизнес-правила заказов ---
    max_cargo_weight_g: int = 2000
    max_cargo_dim_mm: int = 400
    max_distance_m: int = 15000
    min_window_minutes: int = 15
    allowed_cargo_types: List[str] = field(default_factory=lambda: [
        "documents", "parcel", "food", "medical_supplies", "spare_parts"])
    prohibited_keywords: List[str] = field(default_factory=lambda: [
        "оруж", "взрыв", "боеприпас", "пиротех", "наркот", "weapon", "explosive", "ammunition"])
    service_area: List[float] = field(default_factory=lambda: [55.55, 37.35, 55.95, 37.90])  # lat_min, lon_min, lat_max, lon_max
    no_fly_zones: List[NoFlyZone] = field(default_factory=lambda: [
        NoFlyZone("Пример: зона аэродрома", 55.6, 37.40, 3000.0)])
    price_base: float = 300.0
    price_per_km: float = 60.0
    price_per_kg: float = 40.0
    health_check_max_age_h: int = 24
    min_battery_pct: int = 40
    drone_online_timeout_s: int = 30
    link_degraded_s: int = 10
    link_loss_abort_s: int = 120
    receipt_timeout_h: int = 24
    dispute_window_days: int = 7

    # --- видео ---
    video_buffer_seconds: int = 15
    video_spool_dir: str = "/var/lib/fpv/video-spool"
    video_retention_days: int = 180
    video_max_segment_bytes: int = 8 * 1024 * 1024
    ffmpeg_path: str = "ffmpeg"
    live_window_segments: int = 4                # сегментов в списке воспроизведения трансляции
    map_max_bytes: int = 50 * 1024 * 1024        # предельный размер изображения карты

    # --- хранилище объектов ---
    object_store: str = "filesystem"            # filesystem | s3
    object_store_root: str = "/srv/fpv/objects"
    s3_endpoint: str = ""
    s3_bucket: str = "fpv-evidence"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_ca_file: str = ""

    # --- телеметрия и обслуживание ---
    telemetry_retention_days: int = 365
    run_background_in_app: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.environment not in ("development", "test", "production"):
            raise ConfigError("FPV_ENVIRONMENT: допустимы development, test, production")
        if self.directory_provider not in ("ldap", "static"):
            raise ConfigError("FPV_DIRECTORY_PROVIDER: допустимы ldap, static")
        if self.is_production:
            if self.directory_provider != "ldap":
                raise ConfigError("В production допускается только доменная аутентификация (ldap)")
            if not self.cookie_secure:
                raise ConfigError("В production cookie должны передаваться только по HTTPS")
            if self.ldap_uri.startswith("ldap://") and not self.ldap_start_tls:
                raise ConfigError("В production соединение с каталогом должно быть защищено (ldaps:// или StartTLS)")
        if self.object_store not in ("filesystem", "s3"):
            raise ConfigError("FPV_OBJECT_STORE: допустимы filesystem, s3")
        if not 2 <= self.live_window_segments <= 10:
            raise ConfigError("FPV_LIVE_WINDOW_SEGMENTS: допустимо от 2 до 10")
        if self.video_buffer_seconds <= 0:
            raise ConfigError("FPV_VIDEO_BUFFER_SECONDS должно быть положительным")


def _parse_no_fly(value: Optional[str], default: List[NoFlyZone]) -> List[NoFlyZone]:
    """Формат: ``имя:lat:lon:radius_m;имя2:...``."""
    if value is None or value.strip() == "":
        return list(default)
    zones = []
    for chunk in value.split(";"):
        if not chunk.strip():
            continue
        parts = chunk.split(":")
        if len(parts) != 4:
            raise ConfigError("FPV_NO_FLY_ZONES: ожидается 'имя:lat:lon:radius_m;...'")
        zones.append(NoFlyZone(parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    return zones


def load_settings() -> Settings:
    d = Settings()
    s = Settings(
        environment=_str("ENVIRONMENT", d.environment),
        database_url=_str("DATABASE_URL", d.database_url),
        directory_provider=_str("DIRECTORY_PROVIDER", d.directory_provider),
        static_directory_file=_str("STATIC_DIRECTORY_FILE", d.static_directory_file),
        ldap_uri=_str("LDAP_URI", d.ldap_uri),
        ldap_start_tls=_bool("LDAP_START_TLS", d.ldap_start_tls),
        ldap_ca_file=_str("LDAP_CA_FILE", d.ldap_ca_file),
        ldap_base_dn=_str("LDAP_BASE_DN", d.ldap_base_dn),
        ldap_bind_dn=_str("LDAP_BIND_DN", d.ldap_bind_dn),
        ldap_bind_password=_str("LDAP_BIND_PASSWORD", d.ldap_bind_password),
        ldap_user_filter=_str("LDAP_USER_FILTER", d.ldap_user_filter),
        ldap_attr_username=_str("LDAP_ATTR_USERNAME", d.ldap_attr_username),
        ldap_attr_object_id=_str("LDAP_ATTR_OBJECT_ID", d.ldap_attr_object_id),
        ldap_attr_display_name=_str("LDAP_ATTR_DISPLAY_NAME", d.ldap_attr_display_name),
        ldap_attr_email=_str("LDAP_ATTR_EMAIL", d.ldap_attr_email),
        ldap_attr_upn=_str("LDAP_ATTR_UPN", d.ldap_attr_upn),
        ldap_attr_disabled=_str("LDAP_ATTR_DISABLED", d.ldap_attr_disabled),
        ldap_nested_groups=_bool("LDAP_NESTED_GROUPS", d.ldap_nested_groups),
        ldap_timeout_s=_int("LDAP_TIMEOUT_S", d.ldap_timeout_s),
        directory_revalidate_s=_int("DIRECTORY_REVALIDATE_S", d.directory_revalidate_s),
        session_ttl_s=_int("SESSION_TTL_S", d.session_ttl_s),
        session_idle_s=_int("SESSION_IDLE_S", d.session_idle_s),
        cookie_secure=_bool("COOKIE_SECURE", d.cookie_secure),
        login_rate_per_min=_int("LOGIN_RATE_PER_MIN", d.login_rate_per_min),
        mutation_rate_per_min=_int("MUTATION_RATE_PER_MIN", d.mutation_rate_per_min),
        trusted_proxies=_list("TRUSTED_PROXIES", d.trusted_proxies),
        max_cargo_weight_g=_int("MAX_CARGO_WEIGHT_G", d.max_cargo_weight_g),
        max_cargo_dim_mm=_int("MAX_CARGO_DIM_MM", d.max_cargo_dim_mm),
        max_distance_m=_int("MAX_DISTANCE_M", d.max_distance_m),
        min_window_minutes=_int("MIN_WINDOW_MINUTES", d.min_window_minutes),
        allowed_cargo_types=_list("ALLOWED_CARGO_TYPES", d.allowed_cargo_types),
        prohibited_keywords=_list("PROHIBITED_KEYWORDS", d.prohibited_keywords),
        service_area=[float(x) for x in _list("SERVICE_AREA", [str(v) for v in d.service_area])],
        no_fly_zones=_parse_no_fly(_raw("NO_FLY_ZONES"), d.no_fly_zones),
        price_base=_float("PRICE_BASE", d.price_base),
        price_per_km=_float("PRICE_PER_KM", d.price_per_km),
        price_per_kg=_float("PRICE_PER_KG", d.price_per_kg),
        health_check_max_age_h=_int("HEALTH_CHECK_MAX_AGE_H", d.health_check_max_age_h),
        min_battery_pct=_int("MIN_BATTERY_PCT", d.min_battery_pct),
        drone_online_timeout_s=_int("DRONE_ONLINE_TIMEOUT_S", d.drone_online_timeout_s),
        link_degraded_s=_int("LINK_DEGRADED_S", d.link_degraded_s),
        link_loss_abort_s=_int("LINK_LOSS_ABORT_S", d.link_loss_abort_s),
        receipt_timeout_h=_int("RECEIPT_TIMEOUT_H", d.receipt_timeout_h),
        dispute_window_days=_int("DISPUTE_WINDOW_DAYS", d.dispute_window_days),
        video_buffer_seconds=_int("VIDEO_BUFFER_SECONDS", d.video_buffer_seconds),
        video_spool_dir=_str("VIDEO_SPOOL_DIR", d.video_spool_dir),
        video_retention_days=_int("VIDEO_RETENTION_DAYS", d.video_retention_days),
        video_max_segment_bytes=_int("VIDEO_MAX_SEGMENT_BYTES", d.video_max_segment_bytes),
        ffmpeg_path=_str("FFMPEG_PATH", d.ffmpeg_path),
        live_window_segments=_int("LIVE_WINDOW_SEGMENTS", d.live_window_segments),
        map_max_bytes=_int("MAP_MAX_BYTES", d.map_max_bytes),
        object_store=_str("OBJECT_STORE", d.object_store),
        object_store_root=_str("OBJECT_STORE_ROOT", d.object_store_root),
        s3_endpoint=_str("S3_ENDPOINT", d.s3_endpoint),
        s3_bucket=_str("S3_BUCKET", d.s3_bucket),
        s3_access_key=_str("S3_ACCESS_KEY", d.s3_access_key),
        s3_secret_key=_str("S3_SECRET_KEY", d.s3_secret_key),
        s3_ca_file=_str("S3_CA_FILE", d.s3_ca_file),
        telemetry_retention_days=_int("TELEMETRY_RETENTION_DAYS", d.telemetry_retention_days),
        run_background_in_app=_bool("RUN_BACKGROUND_IN_APP", d.run_background_in_app),
    )
    s.validate()
    return s
