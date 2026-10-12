"""Разграничение доступа к персональным и общим сетевым папкам.

Проверяется утверждённая таблица прав (group_vars/all.yml) и конфигурации,
которые из неё генерируются: smb.conf файлового сервера и pam_mount рабочих станций.
Проверка на реальной инфраструктуре выполняется ролью fpv_verify (ACL) и
сценарием docs/storage.md (smbclient -k под разными доменными пользователями).
"""
from __future__ import annotations

import configparser
import importlib.util
import re
from pathlib import Path

import jinja2
import pytest
import yaml

ANS = Path(__file__).resolve().parents[2] / "deploy" / "ansible"
spec = importlib.util.spec_from_file_location("share_policy", ANS / "filter_plugins" / "share_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

GROUPS = {"customers": "FPV-Customers", "operators": "FPV-Operators", "coordinators": "FPV-Coordinators",
          "technicians": "FPV-Technicians", "admins": "FPV-Admins", "linux_admins": "FPV-Linux-Admins",
          "backup_operators": "FPV-Backup-Operators"}


def _render_vars():
    raw = (ANS / "group_vars" / "all.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    env = jinja2.Environment()
    shares = []
    for s in data["fpv_network_shares"]:
        shares.append({k: ([env.from_string(g).render(fpv_domain_groups=GROUPS) for g in v] if isinstance(v, list) else v)
                       for k, v in s.items()})
    return data, shares


@pytest.fixture(scope="module")
def shares():
    return _render_vars()[1]


def test_access_matrix(shares):
    ops = policy.effective_access(shares, "FPV-Operators")
    assert ops == {"fpv-operations": "rw", "fpv-maintenance": "ro", "fpv-admin": "none", "fpv-common": "ro"}
    tech = policy.effective_access(shares, "FPV-Technicians")
    assert tech == {"fpv-operations": "none", "fpv-maintenance": "rw", "fpv-admin": "none", "fpv-common": "ro"}
    coord = policy.effective_access(shares, "FPV-Coordinators")
    assert coord["fpv-operations"] == "rw" and coord["fpv-maintenance"] == "none" and coord["fpv-admin"] == "none"
    adm = policy.effective_access(shares, "FPV-Admins")
    assert adm == {"fpv-operations": "ro", "fpv-maintenance": "ro", "fpv-admin": "rw", "fpv-common": "rw"}


def test_default_deny_for_unlisted_groups(shares):
    for group in ("FPV-Customers", "Domain Users", "FPV-Linux-Admins", ""):
        assert set(policy.effective_access(shares, group).values()) == {"none"}, group


def test_write_overrides_read_and_validation():
    s = policy.normalize_shares([{"name": "x-share", "path": "/srv/shares/x", "read_write": ["A"], "read_only": ["A", "B"]}])
    assert s[0]["read_only"] == ["B"]
    bad = [
        [{"name": "Bad Name", "path": "/srv/shares/x", "read_write": ["A"]}],
        [{"name": "ok", "path": "/etc/passwd", "read_write": ["A"]}],
        [{"name": "ok", "path": "/srv/shares/../etc", "read_write": ["A"]}],
        [{"name": "ok", "path": "/srv/shares/x", "read_write": [], "read_only": []}],
        [{"name": "dup", "path": "/srv/shares/a", "read_write": ["A"]}, {"name": "dup", "path": "/srv/shares/b", "read_write": ["A"]}],
    ]
    for case in bad:
        with pytest.raises(policy.SharePolicyError):
            policy.normalize_shares(case)


def _jinja():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ANS / "roles" / "fpv_network_shares" / "templates")),
                             trim_blocks=True)
    env.filters.update({"fpv_smb_group_list": policy.smb_group_list, "fpv_acl_entries": policy.acl_entries})
    return env


def test_generated_smb_conf_enforces_policy(shares):
    text = _jinja().get_template("smb.conf.j2").render(
        ansible_managed="test", fpv_samba_workgroup="FPV", fpv_domain_realm="FPV.LOCAL", fpv_homes_root="/srv/homes",
        fpv_home_shares_enabled=True, fpv_shares_normalized=policy.normalize_shares(shares))
    cp = configparser.RawConfigParser(strict=True, delimiters=("=",), comment_prefixes=("#", ";"))
    cp.read_string(text)
    g = cp["global"]
    assert g["smb encrypt"] == "required" and g["server min protocol"] == "SMB3_00"
    assert g["map to guest"] == "never" and g["restrict anonymous"] == "2"
    homes = cp["homes"]
    assert homes["valid users"] == "%S" and homes["browseable"] == "no"
    assert homes["create mask"] == "0600" and homes["directory mask"] == "0700"
    ops = cp["fpv-operations"]
    assert ops["read only"] == "yes"
    assert '@"FPV\\FPV-Operators"' in ops["write list"] and '@"FPV\\FPV-Coordinators"' in ops["write list"]
    assert '@"FPV\\FPV-Admins"' in ops["read list"] and "Technicians" not in ops["valid users"]
    adm = cp["fpv-admin"]
    assert adm["valid users"] == '@"FPV\\FPV-Admins"' and "read list" not in adm
    for name in ("fpv-operations", "fpv-maintenance", "fpv-admin", "fpv-common"):
        assert "Customers" not in cp[name]["valid users"]


def test_personal_folder_script_is_owner_only():
    script = (ANS / "roles" / "fpv_network_shares" / "files" / "fpv-mkhome").read_text()
    assert "mkdir -m 0700" in script and 'chown "$user":' in script
    assert re.search(r"\*/\*\|\*\.\.\*", script), "защита от обхода каталогов"


@pytest.mark.parametrize("group,expected", [
    ("FPV-Operators", {"fpv-operations": "rw", "fpv-maintenance": "ro", "fpv-common": "ro"}),
    ("FPV-Technicians", {"fpv-maintenance": "rw", "fpv-common": "ro"}),
    ("FPV-Customers", {}),
])
def test_workstation_mounts_only_permitted_shares(shares, group, expected):
    client = policy.shares_for_group(shares, group)
    xml = _jinja().get_template("pam_mount.conf.xml.j2").render(
        ansible_managed="test", fpv_fileserver_host="fs1.dc.fpv.local", fpv_shares_mount_base="~/Сетевые папки",
        fpv_home_shares_enabled=True, fpv_client_shares=client, fpv_workstation_group=group)
    mounted = dict(re.findall(r'path="(fpv-[a-z-]+)".*?options="[^"]*?(,ro)?"', xml, re.S))
    assert set(mounted) == set(expected)
    for name, access in expected.items():
        assert (mounted[name] == ",ro") == (access == "ro"), name
    assert 'path="%(USER)"' in xml                       # персональная папка — только своя
    assert "sec=krb5" in xml and "seal" in xml and "password" not in xml.lower()
