#!/bin/bash
# Проверка установки пакетов через apt в контейнере Debian 13 (запускается из tests/packaging).
# Ожидает: /repo, /repo2 — подписанные репозитории (версии -1 и -2), /fpv-local.sources,
# FPV_TEST_DATABASE_URL — пустая БД PostgreSQL 13+.
set -u
step() { echo; echo "=== $*"; }
ln -sf /usr/local/bin/python3 /usr/bin/python3   # только для теста: интерпретатор образа
mv /etc/apt/sources.list.d/debian.sources /root/debian.sources.disabled
mkdir -p /srv && cp -a /repo /srv/packages
install -m 0644 /srv/packages/fpv-local.gpg /usr/share/keyrings/fpv-local.gpg
cp /fpv-local.sources /etc/apt/sources.list.d/fpv-local.sources

step "apt update (подпись репозитория)"
apt-get update 2>&1 | tail -3
apt-cache policy fpv-platform | head -4

step "apt install"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends fpv-platform fpv-drone-agent 2>&1 | grep -v "^Get:\|^Selecting\|^Preparing\|^Unpacking\|Reading" | tail -12
echo "RC_INSTALL=${PIPESTATUS[0]}"
ls -ld /etc/fpv /etc/fpv/secrets /var/lib/fpv /srv/fpv-evidence /usr/lib/fpv-platform/venv
id fpv; id fpv-agent

step "настройка БД и dpkg-reconfigure (миграции)"
ls -l /etc/fpv/secrets
printf '%s' "$FPV_TEST_DATABASE_URL" > /etc/fpv/secrets/database_url
DEBIAN_FRONTEND=noninteractive dpkg-reconfigure fpv-platform 2>&1 | tail -4; echo "RC_RECONF=$?"
fpv-migrate --check; echo "RC_CHECK=$?"

step "запуск API от пользователя fpv"
set -a; . /etc/default/fpv-platform; . /etc/fpv/fpv.env; set +a
runuser -u fpv --preserve-environment -- /usr/lib/fpv-platform/venv/bin/uvicorn --factory fpv.main:app_from_env \
   --host 127.0.0.1 --port 8099 > /tmp/api.log 2>&1 &
sleep 5
python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8099/health/ready').read().decode())"
python3 -c "import urllib.request,json;r=urllib.request.Request('http://127.0.0.1:8099/api/v1/auth/login',data=json.dumps({'username':'ivanov','password':'x'}).encode(),headers={'Content-Type':'application/json'});
try: urllib.request.urlopen(r)
except urllib.error.HTTPError as e: print('login без домена:', e.code, e.read().decode()[:160])"
ls /usr/share/fpv-platform/frontend/index.html && head -c 80 /usr/share/fpv-platform/frontend/index.html; echo
fpv-cli verify-audit; echo "RC_AUDIT=$?"
kill %1 2>/dev/null; sleep 1

step "сервисный модуль дрона"
/usr/bin/fpv-agent --help | head -3

step "обновление до 0.1.0-2"
rm -rf /srv/packages && cp -a /repo2 /srv/packages
apt-get update 2>&1 | tail -1
DEBIAN_FRONTEND=noninteractive apt-get install -y --only-upgrade fpv-platform 2>&1 | grep -E "Setting up|fpv-platform:|Применены|миграц" | tail -5
dpkg -l fpv-platform | tail -1

step "удаление и purge (данные должны сохраниться)"
apt-get remove -y fpv-platform >/dev/null 2>&1; echo "RC_REMOVE=$?"
ls -d /usr/lib/fpv-platform/venv 2>&1 | tail -1
apt-get purge -y fpv-platform 2>&1 | grep "fpv-platform:" ; ls /etc/fpv/ /etc/fpv/secrets; ls -ld /var/lib/fpv /srv/fpv-evidence; id fpv
