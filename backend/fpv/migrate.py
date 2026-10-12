"""Применение SQL-миграций из каталога database/migrations.

Каждая миграция применяется в отдельной транзакции и фиксируется в таблице
``schema_migrations`` вместе с контрольной суммой. Изменение уже применённой
миграции обнаруживается и прерывает запуск: схема меняется только новыми файлами.

Запуск: ``python -m fpv.migrate [--dir PATH] [--check]``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "database" / "migrations"


class MigrationError(RuntimeError):
    pass


def _migration_files(directory: Path) -> List[Tuple[str, str, str]]:
    files = sorted(p for p in directory.glob("*.sql") if p.is_file())
    result = []
    for path in files:
        body = path.read_text(encoding="utf-8")
        result.append((path.name, hashlib.sha256(body.encode("utf-8")).hexdigest(), body))
    return result


def migrate(engine: Engine, directory: Path = DEFAULT_DIR, check_only: bool = False) -> List[str]:
    """Применяет недостающие миграции. Возвращает список применённых (или ожидающих при check_only)."""
    if not directory.is_dir():
        raise MigrationError(f"Каталог миграций не найден: {directory}")
    applied_now = []
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"))
    with engine.connect() as conn:
        applied = {row.name: row.checksum for row in conn.execute(
            text("SELECT name, checksum FROM schema_migrations"))}
    for name, checksum, body in _migration_files(directory):
        if name in applied:
            if applied[name] != checksum:
                raise MigrationError(
                    f"Миграция {name} изменена после применения (контрольная сумма не совпадает)")
            continue
        if check_only:
            applied_now.append(name)
            continue
        with engine.begin() as conn:
            # Сериализация параллельных запусков миграций на нескольких узлах.
            conn.execute(text("SELECT pg_advisory_xact_lock(7243001)"))
            already = conn.execute(text("SELECT 1 FROM schema_migrations WHERE name = :n"), {"n": name}).first()
            if already:
                continue
            # Выполнение «как есть» через драйвер, без обработки плейсхолдеров (в SQL встречается '%').
            with conn.connection.driver_connection.cursor() as cur:
                cur.execute(body)
            conn.execute(text("INSERT INTO schema_migrations (name, checksum) VALUES (:n, :c)"),
                         {"n": name, "c": checksum})
        applied_now.append(name)
    return applied_now


def main(argv=None) -> int:
    from .config import load_settings
    from .db import init_engine

    parser = argparse.ArgumentParser(description="Применение миграций PostgreSQL")
    parser.add_argument("--dir", default=os.environ.get("FPV_MIGRATIONS_DIR", str(DEFAULT_DIR)))
    parser.add_argument("--check", action="store_true", help="только показать неприменённые миграции")
    args = parser.parse_args(argv)
    settings = load_settings()
    engine = init_engine(settings.database_url)
    try:
        names = migrate(engine, Path(args.dir), check_only=args.check)
    except MigrationError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    label = "Ожидают применения" if args.check else "Применены"
    print(f"{label}: {', '.join(names) if names else 'нет'}")
    return 1 if (args.check and names) else 0


if __name__ == "__main__":
    sys.exit(main())
