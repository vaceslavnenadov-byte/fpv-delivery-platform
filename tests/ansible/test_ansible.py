"""Автоматические проверки Ansible-комплекта.

Требуется venv с ansible-core 2.21 (переменная FPV_ANSIBLE_VENV, по умолчанию
deploy/ansible/venv) и коллекции в deploy/ansible/collections (или FPV_PACKAGES_DIR
с архивами — будут распакованы во временный каталог).
Контейнерный тест установки включается FPV_TEST_DOCKER=1.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANS = ROOT / "deploy" / "ansible"
SCRIPTS = ANS / "scripts"
PACKAGES = Path(os.environ.get("FPV_PACKAGES_DIR", ROOT.parent / "fpv-delivery-packages"))
VENV = Path(os.environ.get("FPV_ANSIBLE_VENV", ANS / "venv"))
PLAYBOOKS = ["site", "base", "domain", "database", "storage", "application", "workstation", "verify"]
INVENTORIES = ["local", "staging", "production", "ci"]


def _bin(name: str) -> str:
    path = VENV / "bin" / name
    if not path.exists():
        pytest.skip(f"нет {path} (задайте FPV_ANSIBLE_VENV)")
    return str(path)


@pytest.fixture(scope="session")
def collections_path(tmp_path_factory) -> str:
    local = ANS / "collections"
    if (local / "ansible_collections" / "ansible" / "posix").is_dir():
        return str(local)
    cdir = PACKAGES / "ansible" / "collections"
    if not cdir.is_dir():
        pytest.skip("нет коллекций (deploy/ansible/collections или FPV_PACKAGES_DIR)")
    out = tmp_path_factory.mktemp("collections")
    for tarball in cdir.glob("*.tar.gz"):
        ns, rest = tarball.name[:-7].split("-", 1)
        name = rest.rsplit("-", 1)[0]
        dest = out / "ansible_collections" / ns / name
        dest.mkdir(parents=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(dest, filter="data")
    return str(out)


def _env(collections_path: str, tmp_path: Path) -> dict:
    env = dict(os.environ)
    env.update(ANSIBLE_CONFIG=str(ANS / "ansible.cfg"), ANSIBLE_COLLECTIONS_PATH=collections_path,
               ANSIBLE_LOG_PATH=str(tmp_path / "ansible.log"), ANSIBLE_NOCOLOR="1")
    return env


# ---------------------------------------------------------------------------
# Синтаксис и структура
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("inventory", INVENTORIES)
def test_playbooks_syntax(inventory, collections_path, tmp_path):
    for pb in PLAYBOOKS:
        r = subprocess.run([_bin("ansible-playbook"), "-i", f"inventory/{inventory}/hosts.yml", "--syntax-check",
                            f"playbooks/{pb}.yml"], cwd=ANS, env=_env(collections_path, tmp_path),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"{inventory}/{pb}: {r.stdout[-2000:]}{r.stderr[-2000:]}"


def test_roles_have_required_structure():
    roles = [p for p in (ANS / "roles").iterdir() if p.is_dir()]
    expected = {"fpv_base", "fpv_domain", "fpv_users_groups", "fpv_network_shares", "fpv_application",
                "fpv_postgresql", "fpv_logging", "fpv_monitoring", "fpv_verify", "fpv_workstation",
                "fpv_drone_agent"}
    assert expected <= {r.name for r in roles}
    for role in roles:
        assert (role / "defaults" / "main.yml").is_file(), role.name
        assert (role / "tasks" / "main.yml").is_file(), role.name
        assert (role / "meta" / "main.yml").is_file(), role.name
        yaml.safe_load((role / "defaults" / "main.yml").read_text(encoding="utf-8"))


def _all_tasks():
    for path in (ANS / "roles").rglob("tasks/*.yml"):
        for task in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            yield path, task


def test_no_shell_module_and_commands_are_guarded():
    """shell не используется; command/script — только с явными условиями или проверкой результата."""
    for path, task in _all_tasks():
        assert not any(k in task for k in ("shell", "ansible.builtin.shell", "raw")), (path, task.get("name"))
        if any(k in task for k in ("command", "ansible.builtin.command", "script")):
            body = task.get("ansible.builtin.command") or task.get("command") or {}
            guarded = ("changed_when" in task or "when" in task or "creates" in str(body)
                       or task.get("args", {}).get("creates"))
            assert guarded, f"{path}: задача '{task.get('name')}' без условия/changed_when"


def test_secret_tasks_use_no_log():
    for path, task in _all_tasks():
        text = json.dumps(task, ensure_ascii=False)
        if re.search(r"vault_|_password|fpv_agent_key|database_url", text) and "stdin" in text \
                or "secrets/" in json.dumps(task.get("ansible.builtin.copy", {}), ensure_ascii=False):
            assert task.get("no_log") is True, f"{path}: {task.get('name')}"


def test_become_only_where_needed():
    for path in (ANS / "playbooks").glob("*.yml"):
        for play in yaml.safe_load(path.read_text(encoding="utf-8")):
            assert "become" not in play, f"{path}: become на уровне плея запрещён (только на задачах)"
    cfg = (ANS / "ansible.cfg").read_text(encoding="utf-8")
    assert re.search(r"^become = False", cfg, re.M)


def test_dependencies_pinned():
    req = (ANS / "requirements.txt").read_text(encoding="utf-8")
    pkgs = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)", req, re.M)
    assert ("ansible-core", "2.21.5") in pkgs
    assert req.count("--hash=sha256:") >= len(pkgs)
    coll = yaml.safe_load((ANS / "requirements.yml").read_text(encoding="utf-8"))["collections"]
    assert all(re.match(r"^\d+\.\d+\.\d+$", c["version"]) for c in coll)
    gv = yaml.safe_load((ANS / "group_vars" / "all.yml").read_text(encoding="utf-8"))
    assert re.match(r"^v\d{4}\.\d{2}\.\d+$", gv["fpv_packages_ref"]), "ссылка на конкретный тег пакетов"


def test_packages_repo_matches_reference():
    if not (PACKAGES / "VERSION").is_file():
        pytest.skip("нет fpv-delivery-packages")
    gv = yaml.safe_load((ANS / "group_vars" / "all.yml").read_text(encoding="utf-8"))
    assert (PACKAGES / "VERSION").read_text().strip() == gv["fpv_packages_ref"]
    r = subprocess.run(["sha256sum", "--quiet", "--strict", "-c", "SHA256SUMS"], cwd=PACKAGES / "ansible" / "collections",
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# Назначение ролей по типам устройств
# ---------------------------------------------------------------------------

def _plays_hosts(collections_path, tmp_path, inventory="production"):
    r = subprocess.run([_bin("ansible-playbook"), "-i", f"inventory/{inventory}/hosts.yml", "--list-tasks",
                        "--list-hosts", "playbooks/site.yml"], cwd=ANS, env=_env(collections_path, tmp_path),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr[-2000:]
    plays, current = [], None
    for line in r.stdout.splitlines():
        m = re.match(r"\s*play #\d+ \(([^)]*)\): (.*?)\s+TAGS", line)
        if m:
            current = {"pattern": m.group(1), "name": m.group(2), "hosts": set(), "roles": set()}
            plays.append(current)
            continue
        if current is None:
            continue
        hm = re.match(r"^\s{6}([A-Za-z0-9.-]+\.[a-z]+)\s*$", line)
        if hm:
            current["hosts"].add(hm.group(1))
        rm = re.match(r"^\s{6}(fpv_[a-z_]+) : ", line)
        if rm:
            current["roles"].add(rm.group(1))
    host_roles = {}
    for p in plays:
        for h in p["hosts"]:
            host_roles.setdefault(h, set()).update(p["roles"])
    return host_roles


def test_each_device_type_gets_only_its_roles(collections_path, tmp_path):
    hr = _plays_hosts(collections_path, tmp_path)
    common = {"fpv_base", "fpv_users_groups", "fpv_logging", "fpv_monitoring", "fpv_domain", "fpv_verify"}
    expect = {
        "app1.dc.fpv.local": common | {"fpv_application"},
        "db1.dc.fpv.local": common | {"fpv_postgresql"},
        "fs1.dc.fpv.local": common | {"fpv_network_shares"},
        "mon1.dc.fpv.local": common,
        "ws-ops-01.fpv.local": common | {"fpv_network_shares", "fpv_workstation"},
        "ws-adm-01.fpv.local": common | {"fpv_network_shares", "fpv_workstation"},
        "gs-01.fpv.local": common | {"fpv_drone_agent"},
    }
    for host, roles in expect.items():
        assert hr.get(host) == roles, f"{host}: {sorted(hr.get(host, []))} != {sorted(roles)}"
    forbidden_for_ws = {"fpv_application", "fpv_postgresql"}
    for host, roles in hr.items():
        if host.startswith("ws-"):
            assert not (roles & forbidden_for_ws), host


def test_inventory_overrides_take_effect(collections_path, tmp_path):
    """Переопределения окружения не перекрываются playbook group_vars/all."""
    def host_vars(inv, host):
        r = subprocess.run([_bin("ansible-inventory"), "-i", f"inventory/{inv}/hosts.yml", "--playbook-dir", "playbooks",
                            "--host", host], cwd=ANS, env=_env(collections_path, tmp_path), capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    ci = host_vars("ci", "localhost")
    assert ci["fpv_manage_packages"] is False and ci["fpv_environment"] == "test"
    assert host_vars("staging", "stage1.test.fpv.local")["fpv_environment"] == "test"
    assert host_vars("production", "app1.dc.fpv.local")["fpv_environment"] == "production"
    ws = host_vars("production", "ws-ops-01.fpv.local")
    # ansible-inventory не раскрывает шаблоны: проверяем ссылку на группу операторов
    assert ws["fpv_login_groups"] == ["{{ fpv_domain_groups.operators }}", "{{ fpv_domain_groups.linux_admins }}"]


# ---------------------------------------------------------------------------
# Shell-скрипты: проверка аргументов и кодов завершения
# ---------------------------------------------------------------------------

def _run(script, *args, env=None):
    return subprocess.run(["bash", str(SCRIPTS / script), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL, timeout=60)


@pytest.mark.parametrize("script,args", [
    ("run-playbook.sh", []),
    ("run-playbook.sh", ["--mode", "local"]),
    ("run-playbook.sh", ["--mode", "remote", "--inventory", "inventory/local/hosts.yml", "--playbook", "site"]),
    ("run-playbook.sh", ["--mode", "local", "--inventory", "inventory/local/hosts.yml", "--playbook", "rm-rf"]),
    ("run-playbook.sh", ["--unknown"]),
    ("install.sh", []),
    ("install.sh", ["--mode", "local", "--playbook", "site"]),
    ("install.sh", ["--mode", "local", "--inventory", "x", "--playbook", "site", "--venv", "relative/venv"]),
    ("preflight.sh", ["--mode", "both"]),
    ("postflight.sh", ["--mode", "local"]),
    ("rollback.sh", ["--component", "database"]),
    ("rollback.sh", ["--component", "application", "--to", "../../etc"]),
])
def test_scripts_reject_invalid_arguments(script, args):
    r = _run(script, *[str(ANS / a) if a.startswith("inventory/") else a for a in args])
    assert r.returncode == 2, (script, args, r.returncode, r.stdout, r.stderr)


def _fake_venv(tmp_path):
    """Подставной venv: записывает аргументы вызова ansible-playbook и возвращает заданный код."""
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    log = tmp_path / "calls.log"
    (venv / "ansible-playbook").write_text(
        "#!/bin/bash\necho \"$@\" >> %s\nexit ${FAKE_RC:-0}\n" % log)
    (venv / "ansible-inventory").write_text(
        "#!/bin/bash\necho '{\"ansible_connection\": \"local\"}'\n")
    (venv / "python").symlink_to(sys.executable)
    for f in venv.iterdir():
        f.chmod(0o755)
    return tmp_path / "venv", log


def test_run_playbook_uses_venv_and_propagates_errors(tmp_path):
    venv, log = _fake_venv(tmp_path)
    inv = str(ANS / "inventory" / "local" / "hosts.yml")
    env = {"ANSIBLE_LOG_PATH": str(tmp_path / "a.log")}
    r = _run("run-playbook.sh", "--mode", "local", "--inventory", inv, "--playbook", "base", "--venv", str(venv),
             "--check", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    call = log.read_text()
    assert "--limit localhost" in call and "playbooks/base.yml" in call and "--check --diff" in call
    r = _run("run-playbook.sh", "--mode", "local", "--inventory", inv, "--playbook", "base", "--venv", str(venv),
             env={**env, "FAKE_RC": "2"})
    assert r.returncode == 6 and "завершился с кодом 2" in r.stdout + r.stderr


def test_central_mode_requires_explicit_targets(tmp_path):
    venv, _ = _fake_venv(tmp_path)
    inv = str(ANS / "inventory" / "production" / "hosts.yml")
    r = _run("run-playbook.sh", "--mode", "central", "--inventory", inv, "--playbook", "site", "--venv", str(venv))
    assert r.returncode == 2 and "--limit" in r.stdout + r.stderr
    r = _run("run-playbook.sh", "--mode", "central", "--inventory", inv, "--playbook", "site", "--venv", str(venv),
             "--limit", "localhost")
    assert r.returncode == 2


def test_secrets_not_accepted_in_plain_files(tmp_path):
    venv, _ = _fake_venv(tmp_path)
    inv = str(ANS / "inventory" / "local" / "hosts.yml")
    extra = tmp_path / "extra.yml"
    extra.write_text("fpv_app_database_password: Pl41nText\n")
    r = _run("run-playbook.sh", "--mode", "local", "--inventory", inv, "--playbook", "base", "--venv", str(venv),
             "--extra-vars-file", str(extra))
    assert r.returncode == 2 and "Vault" in r.stdout + r.stderr
    vault = tmp_path / "vault-pass"
    vault.write_text("x")
    vault.chmod(0o644)
    r = _run("run-playbook.sh", "--mode", "local", "--inventory", inv, "--playbook", "base", "--venv", str(venv),
             "--vault-password-file", str(vault))
    assert r.returncode == 2 and "небезопасные права" in r.stdout + r.stderr


def test_rollback_application_switches_release(tmp_path):
    root = tmp_path / "opt-fpv"
    for rel in ("0.1.0-aaa", "0.1.0-bbb"):
        d = root / "releases" / rel
        (d / "venv" / "bin").mkdir(parents=True)
        (d / "venv" / "bin" / "python").write_text("")
        (d / "venv" / "bin" / "python").chmod(0o755)
        (d / "backend" / "fpv").mkdir(parents=True)
        (d / "backend" / "fpv" / "__init__.py").write_text("")
    (root / "current").symlink_to(root / "releases" / "0.1.0-bbb")
    (root / "previous-release").write_text(str(root / "releases" / "0.1.0-aaa") + "\n")
    env = {"FPV_INSTALL_ROOT": str(root)}
    r = _run("rollback.sh", "--component", "application", env=env)            # без --confirm — только проверка
    assert r.returncode == 0 and os.readlink(root / "current").endswith("0.1.0-bbb")
    r = _run("rollback.sh", "--component", "application", "--confirm", "--no-restart", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.readlink(root / "current").endswith("0.1.0-aaa")
    assert (root / "previous-release").read_text().strip().endswith("0.1.0-bbb")


def test_scripts_do_not_suppress_errors():
    for path in SCRIPTS.glob("*.sh"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "|| true" in line:
                assert "#" in line or "readlink" in line or "ls -1" in line or "logger" in line, f"{path.name}:{i}: {line}"


def test_scripts_shellcheck_clean():
    sc = shutil.which("shellcheck") or str(VENV / "bin" / "shellcheck")
    if not os.path.exists(sc):
        pytest.skip("shellcheck не установлен")
    r = subprocess.run([sc, "-x", "-P", str(SCRIPTS), *map(str, SCRIPTS.glob("*.sh"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_install_rejects_tampered_bundle(tmp_path):
    bundle = tmp_path / "ancible"
    shutil.copytree(ANS / "scripts", bundle)
    shutil.copy(ANS / "ansible.cfg", bundle)
    (bundle / "payload.txt").write_text("original")
    subprocess.run("sha256sum ansible.cfg payload.txt > SHA256SUMS", shell=True, cwd=bundle, check=True)
    (bundle / "payload.txt").write_text("tampered")
    r = subprocess.run(["bash", str(bundle / "install.sh"), "--mode", "local", "--inventory", "x", "--playbook", "site"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 4 and "контрольные суммы" in r.stdout + r.stderr
    logs = list((bundle / "logs").glob("install-*.log"))
    assert logs and stat.S_IMODE(logs[0].stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# Полная установка в контейнере Debian 13 (без сети)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("FPV_TEST_DOCKER") != "1", reason="FPV_TEST_DOCKER=1 для контейнерного теста")
def test_offline_install_in_debian13_container_is_idempotent(tmp_path):
    out = tmp_path / "bundle"
    r = subprocess.run(["bash", str(SCRIPTS / "build-bundle.sh"), "--packages-dir", str(PACKAGES), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    script = tmp_path / "t.sh"
    script.write_text(
        "set -u\ncd /tmp && tar -xzf /b/fpv-ancible-*.tar.gz\ncd /tmp/ancible\n"
        "for i in 1 2; do ./install.sh --mode local --inventory inventory/ci/hosts.yml --playbook site --offline"
        " > /tmp/run$i.log 2>&1; echo RC$i=$?; grep -A1 'PLAY RECAP' /tmp/run$i.log | tail -1; done\n"
        "python3 -c \"import json;r=json.load(open('/var/log/fpv/verify-report.json'));"
        "print('VERIFY', all(c['ok'] for c in r['results']))\"\n")
    for f in out.iterdir():
        f.chmod(0o644)
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{out}:/b:ro", "-v", f"{script}:/t.sh:ro",
                        os.environ.get("FPV_TEST_IMAGE", "mirror.gcr.io/library/python:3.13-slim-trixie"), "bash", "/t.sh"], capture_output=True, text=True, timeout=900)
    assert "RC1=0" in r.stdout and "RC2=0" in r.stdout, r.stdout + r.stderr
    recap2 = r.stdout.split("RC2=0", 1)[1]
    assert re.search(r"changed=0\s", recap2), r.stdout
    assert "VERIFY True" in r.stdout
