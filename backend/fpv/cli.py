"""Служебные команды: ``python -m fpv.cli <команда>``.

  dev-directory  создать файл эмулятора каталога с демонстрационными пользователями (только dev/test)
  sync-directory синхронизировать пользователей с доменом (блокировка → отзыв доступа)
  verify-audit   проверить целостность цепочки журнала аудита
  verify-videos  проверить контрольные суммы всех сохранённых видеоподтверждений
  outbox-stats   состояние очереди событий
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

DEMO_USERS = {
    "customer": ("Анна Заказчикова", ["FPV-Customers"]),
    "operator": ("Олег Операторов", ["FPV-Operators"]),
    "operator2": ("Ольга Пилотова", ["FPV-Operators"]),
    "coordinator": ("Кирилл Координатов", ["FPV-Coordinators"]),
    "technician": ("Тимур Техников", ["FPV-Technicians"]),
    "admin": ("Алла Администраторова", ["FPV-Admins"]),
}
GROUP_DN = "CN={},OU=FPV,OU=Groups,DC=fpv,DC=local"


def cmd_dev_directory(args) -> int:
    from .services.directory import pbkdf2_hash
    if os.environ.get("FPV_ENVIRONMENT") == "production":
        print("ОШИБКА: эмулятор каталога запрещён в production", file=sys.stderr)
        return 2
    password = os.environ.get("FPV_DEV_PASSWORD") or secrets.token_urlsafe(12)
    users = [{"username": name, "object_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"fpv-dev-{name}")),
              "display_name": display, "email": f"{name}@fpv.local",
              "password_pbkdf2": pbkdf2_hash(password), "groups": [GROUP_DN.format(g) for g in groups],
              "enabled": True}
             for name, (display, groups) in DEMO_USERS.items()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"available": True, "users": users}, f, ensure_ascii=False, indent=2)
    print(f"Создан файл эмулятора каталога: {out}")
    print(f"Пользователи: {', '.join(DEMO_USERS)}")
    if not os.environ.get("FPV_DEV_PASSWORD"):
        # Пароль стенда показывается один раз и нигде не сохраняется в открытом виде.
        print(f"Пароль для всех демонстрационных пользователей: {password}")
    return 0


def _bootstrap():
    from . import db, runtime
    from .config import load_settings
    from .services import directory, storage
    s = load_settings()
    runtime.runtime.settings = s
    runtime.runtime.directory = directory.build_provider(s)
    runtime.runtime.store = storage.build_store(s)
    db.init_engine(s.database_url)
    return s


def cmd_sync_directory(_args) -> int:
    from . import db, runtime
    from .services import audit, identity
    _bootstrap()
    with db.session_scope() as s:
        stats = identity.sync_all_users(s, runtime.directory())
        audit.record(s, audit.Actor.system("cli"), "directory.sync", details=stats)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def cmd_verify_audit(_args) -> int:
    from . import db
    from .services import audit
    _bootstrap()
    with db.session_scope() as s:
        result = audit.verify_chain(s)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_verify_videos(_args) -> int:
    from sqlalchemy import select
    from . import db
    from .models import VideoEvidence
    from .services import audit, video
    _bootstrap()
    bad = 0
    with db.session_scope() as s:
        for ev in s.execute(select(VideoEvidence).where(VideoEvidence.status == "stored")).scalars():
            if not video.verify_integrity(s, ev, audit.Actor.system("integrity-check")):
                bad += 1
                print(f"НАРУШЕНА целостность: evidence={ev.id} order={ev.order_id}")
    print(f"Проверка завершена, нарушений: {bad}")
    return 1 if bad else 0


def cmd_outbox_stats(_args) -> int:
    from . import db
    from .services import events
    _bootstrap()
    with db.session_scope() as s:
        print(json.dumps(events.outbox_stats(s)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fpv.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dev-directory")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_dev_directory)
    sub.add_parser("sync-directory").set_defaults(func=cmd_sync_directory)
    sub.add_parser("verify-audit").set_defaults(func=cmd_verify_audit)
    sub.add_parser("verify-videos").set_defaults(func=cmd_verify_videos)
    sub.add_parser("outbox-stats").set_defaults(func=cmd_outbox_stats)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
