"""Пакеты Debian 13: сборка, структура, apt-репозиторий, установка через apt.

Требуется: dpkg-deb, dpkg-scanpackages, собранный frontend (frontend/dist) и каталог wheels
(FPV_WHEELS_DIR, по умолчанию ../fpv-delivery-packages/python/wheels).
Полный цикл apt в контейнере Debian 13: FPV_TEST_DOCKER=1 и FPV_TEST_DEB_DATABASE_URL
(пустая БД, доступная из контейнера с --network host).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packaging" / "debian"
WHEELS = Path(os.environ.get("FPV_WHEELS_DIR", ROOT.parent / "fpv-delivery-packages" / "python" / "wheels"))
IMAGE = os.environ.get("FPV_TEST_IMAGE", "mirror.gcr.io/library/python:3.13-slim-trixie")


def _need(*cmds):
    for c in cmds:
        if not shutil.which(c):
            pytest.skip(f"нет {c}")


def _build(out: Path, revision: int = 1) -> dict:
    _need("dpkg-deb", "dpkg-scanpackages")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        pytest.skip("frontend не собран (npm run build)")
    if not WHEELS.is_dir():
        pytest.skip(f"нет каталога wheels {WHEELS}")
    r = subprocess.run(["bash", str(PKG / "build-debs.sh"), "--wheels", str(WHEELS), "--out", str(out),
                        "--revision", str(revision)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return {p.name.split("_")[0]: p for p in out.glob("*.deb")}


@pytest.fixture(scope="module")
def debs(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("debs"))


def _field(deb: Path, name: str) -> str:
    return subprocess.run(["dpkg-deb", "--field", str(deb), name], capture_output=True, text=True, check=True).stdout.strip()


def _contents(deb: Path):
    out = subprocess.run(["dpkg-deb", "--contents", str(deb)], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        perm, owner, _size, _d, _t, path = line.split(None, 5)
        yield perm, owner, path.split(" -> ")[0].lstrip(".")


def test_control_fields(debs):
    p, a = debs["fpv-platform"], debs["fpv-drone-agent"]
    assert _field(p, "Architecture") == "amd64" and _field(a, "Architecture") == "all"
    assert _field(p, "Version") == _field(a, "Version")
    assert "python3 (>= 3.13)" in _field(p, "Depends") and "python3-venv" in _field(p, "Depends")
    assert int(_field(p, "Installed-Size")) > 1000


def test_files_owner_permissions_and_layout(debs):
    entries = list(_contents(debs["fpv-platform"]))
    paths = {e[2] for e in entries}
    assert all(owner == "root/root" for _p, owner, _ in entries)
    for required in ("/usr/lib/fpv-platform/backend/fpv/main.py", "/usr/lib/fpv-platform/backend/requirements.lock",
                     "/usr/lib/fpv-platform/database/migrations/0001_core_schema.sql",
                     "/usr/share/fpv-platform/frontend/index.html", "/usr/bin/fpv-migrate", "/usr/bin/fpv-cli",
                     "/usr/lib/systemd/system/fpv-api.service", "/usr/lib/systemd/system/fpv-worker.service",
                     "/etc/fpv/fpv.env", "/etc/default/fpv-platform", "/etc/nginx/sites-available/fpv-platform.conf",
                     "/usr/share/doc/fpv-platform/copyright", "/usr/share/doc/fpv-platform/changelog.Debian.gz"):
        assert required in paths, required
    assert not any("__pycache__" in p or p.endswith(".pyc") for p in paths)
    assert not any("/etc/fpv/secrets/" in p for p in paths), "секреты не поставляются в пакете"
    for perm, _o, path in entries:
        if not perm.startswith("-"):
            continue
        expected = "-rwxr-xr-x" if path.startswith("/usr/bin/") else "-rw-r--r--"
        assert perm == expected, path


def test_all_locked_dependencies_are_shipped(debs):
    wheels = {Path(p).name.split("-")[0].lower().replace("_", "-") for _pm, _o, p in _contents(debs["fpv-platform"])
              if p.endswith(".whl")}
    lock = (ROOT / "backend" / "requirements.lock").read_text()
    names = {m.lower().replace("_", "-") for m in re.findall(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==", lock, re.M)}
    assert names <= wheels, names - wheels


def test_maintainer_scripts_and_conffiles(tmp_path, debs):
    for name, deb in debs.items():
        ctl = tmp_path / name
        subprocess.run(["dpkg-deb", "--control", str(deb), str(ctl)], check=True)
        for script in ("postinst", "prerm", "postrm"):
            assert (ctl / script).stat().st_mode & 0o777 == 0o755
            assert subprocess.run(["sh", "-n", str(ctl / script)]).returncode == 0, (name, script)
        conffiles = (ctl / "conffiles").read_text().split()
        shipped = {p for _pm, _o, p in _contents(deb)}
        assert set(conffiles) <= shipped
        assert (ctl / "md5sums").read_text().count("\n") >= 5
    postrm = (tmp_path / "fpv-platform" / "postrm").read_text()
    assert "rm -rf /var/lib/fpv" not in postrm and "/srv/fpv-evidence" not in postrm.split("echo")[0]


def test_nginx_site_is_rendered():
    conf = (PKG / "fpv-platform" / "files" / "etc" / "nginx" / "sites-available" / "fpv-platform.conf").read_text()
    assert "{{" not in conf and "root /usr/share/fpv-platform/frontend;" in conf and "http2 on;" in conf


def test_make_apt_repo_unsigned_and_index(tmp_path, debs):
    repo = tmp_path / "repo"
    r = subprocess.run(["bash", str(PKG / "make-apt-repo.sh"), str(repo), "--unsigned", *map(str, debs.values())],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    packages = (repo / "Packages").read_text()
    assert packages.count("Package: ") == 2 and "Package: fpv-platform" in packages
    release = (repo / "Release").read_text()
    import hashlib
    assert hashlib.sha256((repo / "Packages").read_bytes()).hexdigest() in release
    r = subprocess.run(["bash", str(PKG / "make-apt-repo.sh"), str(tmp_path / "x")], capture_output=True, text=True)
    assert r.returncode != 0 and "--key" in r.stderr     # без подписи только явно


@pytest.mark.skipif(os.environ.get("FPV_TEST_DOCKER") != "1" or not os.environ.get("FPV_TEST_DEB_DATABASE_URL"),
                    reason="FPV_TEST_DOCKER=1 и FPV_TEST_DEB_DATABASE_URL для установки через apt в контейнере")
def test_apt_install_upgrade_remove_in_debian13(tmp_path, debs):
    _need("docker", "gpg")
    gnupg = Path("/tmp") / f"fpvg{os.getpid()}"            # короткий путь для сокета gpg-agent
    gnupg.mkdir(mode=0o700, exist_ok=True)
    env = {**os.environ, "GNUPGHOME": str(gnupg)}
    subprocess.run(["gpg", "--batch", "--quiet", "--passphrase", "", "--quick-gen-key", "FPV Test Repo <t@fpv.local>",
                    "rsa3072", "sign", "1d"], env=env, check=True)
    # Заглушки python3/python3-venv: в образе интерпретатор не из пакета Debian (только для теста).
    stubs = []
    for name in ("python3", "python3-venv"):
        root = tmp_path / "stub" / name / "DEBIAN"
        root.mkdir(parents=True)
        (root / "control").write_text(f"Package: {name}\nVersion: 3.13.5-1\nArchitecture: all\n"
                                      "Maintainer: test <t@t>\nDescription: test stub\n")
        deb = tmp_path / f"{name}_3.13.5-1_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root.parent), str(deb)], check=True,
                       capture_output=True)
        stubs.append(str(deb))
    debs2 = _build(tmp_path / "debs2", revision=2)
    repo1, repo2 = tmp_path / "repo1", tmp_path / "repo2"
    for repo, pkgs in ((repo1, [*map(str, debs.values()), *stubs]),
                       (repo2, [*map(str, debs.values()), *stubs, *map(str, debs2.values())])):
        subprocess.run(["bash", str(PKG / "make-apt-repo.sh"), str(repo), "--key", "FPV Test Repo", *pkgs],
                       env=env, check=True, capture_output=True)
    r = subprocess.run(["docker", "run", "--rm", "--network", "host", "-e", "FPV_TEST_DATABASE_URL",
                        "-v", f"{repo1}:/repo:ro", "-v", f"{repo2}:/repo2:ro",
                        "-v", f"{PKG / 'tests' / 'container-apt-test.sh'}:/test.sh:ro",
                        "-v", f"{PKG / 'fpv-local.sources'}:/fpv-local.sources:ro", IMAGE, "bash", "/test.sh"],
                       capture_output=True, text=True, timeout=900,
                       env={**os.environ, "FPV_TEST_DATABASE_URL": os.environ["FPV_TEST_DEB_DATABASE_URL"]})
    out = r.stdout + r.stderr
    assert "RC_INSTALL=0" in out and "RC_CHECK=0" in out and "RC_AUDIT=0" in out, out[-4000:]
    assert '"database":"ok"' in out and "directory_unavailable" in out
    assert re.search(r"ii\s+fpv-platform\s+0\.1\.0-2", out), out[-3000:]
    assert "cannot access '/usr/lib/fpv-platform/venv'" in out          # venv удалён вместе с пакетом
    assert "drwxr-x--- 2 fpv fpv" in out                                 # данные сохранены после purge
