"""Политика доступа к сетевым ресурсам ЦОД.

Единственный источник истины — таблица ``fpv_network_shares`` (group_vars/all.yml).
Из неё строятся smb.conf (valid users / read list / write list), POSIX ACL
каталогов на файловом сервере и монтирования pam_mount на рабочих станциях.
Принцип: запрещено всё, что не разрешено явно; запись важнее чтения.
"""
from __future__ import annotations

import re

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_PATH_RE = re.compile(r"^/srv/[A-Za-z0-9/_-]+$")


class SharePolicyError(ValueError):
    pass


def normalize_shares(shares):
    """Проверяет таблицу и возвращает нормализованный список ресурсов."""
    result = []
    seen = set()
    for share in shares or []:
        name = share.get("name", "")
        path = share.get("path", "")
        if not _NAME_RE.match(name):
            raise SharePolicyError("Недопустимое имя ресурса: %r" % name)
        if name in seen:
            raise SharePolicyError("Повторяющееся имя ресурса: %s" % name)
        if not _PATH_RE.match(path) or ".." in path:
            raise SharePolicyError("Недопустимый путь ресурса %s: %r" % (name, path))
        seen.add(name)
        rw = sorted(set(share.get("read_write") or []))
        ro = sorted(set(share.get("read_only") or []) - set(rw))
        if not rw and not ro:
            raise SharePolicyError("Ресурсу %s не назначено ни одной группы" % name)
        result.append({"name": name, "path": path, "comment": share.get("comment", ""),
                       "read_write": rw, "read_only": ro, "groups": rw + ro})
    return result


def effective_access(shares, group):
    """{ресурс: 'rw' | 'ro' | 'none'} для доменной группы."""
    access = {}
    for s in normalize_shares(shares):
        if group in s["read_write"]:
            access[s["name"]] = "rw"
        elif group in s["read_only"]:
            access[s["name"]] = "ro"
        else:
            access[s["name"]] = "none"
    return access


def shares_for_group(shares, group):
    """Ресурсы, доступные группе (для монтирования на рабочей станции)."""
    access = effective_access(shares, group)
    return [dict(s, access=access[s["name"]]) for s in normalize_shares(shares) if access[s["name"]] != "none"]


def acl_entries(share, domain_prefix=""):
    """Записи POSIX ACL (обычные и наследуемые) для каталога ресурса."""
    entries = []
    for group in share["read_write"]:
        entries.append({"entity": domain_prefix + group, "permissions": "rwx"})
    for group in share["read_only"]:
        entries.append({"entity": domain_prefix + group, "permissions": "r-x"})
    return entries


def smb_group_list(groups, workgroup=""):
    prefix = (workgroup + "\\") if workgroup else ""
    return " ".join('@"%s%s"' % (prefix, g) for g in groups)


class FilterModule(object):
    def filters(self):
        return {
            "fpv_normalize_shares": normalize_shares,
            "fpv_effective_access": effective_access,
            "fpv_shares_for_group": shares_for_group,
            "fpv_acl_entries": acl_entries,
            "fpv_smb_group_list": smb_group_list,
        }
