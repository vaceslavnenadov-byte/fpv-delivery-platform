# Развёртывание на Debian 11 с помощью Ansible

Комплект установки копируется в `/tmp/ancible` и запускает Ansible из собственного Python venv.
`/tmp/ancible` — только staging-каталог: роли размещают постоянные файлы в `/opt/fpv`,
`/etc/fpv`, `/var/lib/fpv`, `/var/log/fpv`, данные — в ЦОД. Долговременные секреты в `/tmp` не хранятся.

> Перед вводом в эксплуатацию прочитайте `debian11-lifecycle.md`: стандартная поддержка
> Debian 11, PostgreSQL 13 и Python 3.9 на дату выпуска уже завершена.

## 1. Состав комплекта

```text
/tmp/ancible/
├── install.sh            bootstrap: целостность → preflight → venv → зависимости → коллекции → версии → playbook
├── run-playbook.sh       единая точка запуска playbook (Ansible из venv по явному пути)
├── preflight.sh          проверка ОС, архитектуры, прав, диска, Python, DNS, репозиториев
├── postflight.sh         итоговая проверка (verify.yml) и сводка
├── rollback.sh           откат поддерживаемых изменений (приложение, конфигурация)
├── lib.sh                общие функции
├── ansible.cfg  requirements.txt (ansible-core 2.15.13 + хеши)  requirements.yml
├── playbooks/            site base domain database storage application workstation verify
├── roles/                11 ролей (defaults/ tasks/ handlers/ templates/ files/ meta/)
├── inventory/            local | staging | production | ci (+ group_vars окружений)
├── group_vars/ host_vars/ filter_plugins/
├── app/fpv-app-<версия>.tar.gz          backend, миграции, frontend/dist, drone-agent
├── packages/             артефакты fpv-delivery-packages (манифесты, wheels, коллекции, SHA256SUMS)
├── SHA256SUMS  VERSION  INSTALL.md
└── logs/                 журналы установки (права 600)
```

## 2. Подготовка комплекта (на рабочей станции сборки)

```bash
cd fpv-delivery-packages && git checkout v2026.10.0 && scripts/verify.sh
scripts/fetch-wheels.sh                       # для изолированной сети: python/wheels/
cd ../fpv-delivery-platform
deploy/ansible/scripts/build-bundle.sh --packages-dir ../fpv-delivery-packages --build-frontend
# → build/fpv-ancible-0.1.0.tar.gz и .sha256
```

`build-bundle.sh` проверяет, что версия пакетов совпадает с `fpv_packages_ref`, проверяет их
контрольные суммы, исключает файлы Vault и формирует `SHA256SUMS` комплекта.

## 3. Копирование в /tmp/ancible

```bash
scp build/fpv-ancible-0.1.0.tar.gz{,.sha256} admin@узел:/tmp/
ssh admin@узел 'cd /tmp && sha256sum -c fpv-ancible-0.1.0.tar.gz.sha256 && tar -xzf fpv-ancible-0.1.0.tar.gz'
# результат: /tmp/ancible
```

## 4. Режимы запуска (указываются явно)

### 4.1. Локальная установка — Ansible управляет только этим узлом

```bash
cd /tmp/ancible
sudo ./install.sh --mode local --inventory inventory/local/hosts.yml --playbook base \
     --install-system-deps            # только если нет python3-venv; ставит python3-venv и python3-pip
```

Для узла с функциональной ролью используйте inventory, где `localhost` входит в нужную группу
(например, скопируйте `inventory/local/hosts.yml` и добавьте `localhost` в `app_servers`).
`run-playbook.sh` в этом режиме принимает только `localhost` с `ansible_connection: local`.

### 4.2. Централизованное развёртывание — управляющий узел, SSH

```bash
cd /tmp/ancible
./install.sh --mode central --inventory inventory/production/hosts.yml --playbook site \
     --limit 'datacenter' --vault-password-file ~/.fpv-vault-pass
./run-playbook.sh --mode central --inventory inventory/production/hosts.yml --playbook workstation \
     --limit 'operator_workstations' --vault-password-file ~/.fpv-vault-pass
```

Цели (`--limit`) обязательны; `localhost` в этом режиме запрещён (используйте `--mode local`).
Комплект на целевые узлы не копируется — роли передают только нужные артефакты.

### 4.3. Изолированная сеть

`--offline`: Python-пакеты только из `packages/python/wheels` (`pip --no-index --require-hashes`),
коллекции — из `packages/ansible/collections` после проверки SHA-256; пакеты Debian — из
внутреннего зеркала (`fpv_apt_source: mirror`, `fpv_apt_mirror_url`).

## 5. Что делает install.sh

| Этап | Действие | Ошибка → код |
|---|---|---|
| 1 | `sha256sum -c SHA256SUMS` комплекта | 4 |
| 2 | `preflight.sh`: Debian 11, x86_64/aarch64, права/sudo, место, python3 ≥ 3.9, venv, DNS, репозитории | 3 |
| 3 | python3-venv; системные пакеты — только с `--install-system-deps` | 5 |
| 4 | `python3 -m venv /tmp/ancible/venv` (повторно используется) | 5 |
| 5 | `pip install --require-hashes -r requirements.txt` | 5 |
| 6 | коллекции: Galaxy по `requirements.yml` или офлайн-архивы | 4/5 |
| 7 | проверка Python, ansible-core 2.15.x, коллекций | 5 |
| 8 | `run-playbook.sh` → `venv/bin/ansible-playbook` | 6 |

Весь вывод — в `logs/install-<время>.log` (права 600) и, при запуске от root, копия в
`/var/log/fpv/ansible/`. Журнал Ansible — `logs/ansible.log`. Ошибка любого этапа
останавливает установку и возвращает ненулевой код.

## 6. Назначение ролей по типам устройств

| Группа inventory | Плейбуки | Роли |
|---|---|---|
| все узлы | base, domain, verify | fpv_base, fpv_users_groups, fpv_logging, fpv_monitoring, fpv_domain, fpv_verify |
| app_servers | application | fpv_application |
| db_servers | database | fpv_postgresql (+ резервное копирование на `fpv_postgresql_primary`) |
| file_servers | storage | fpv_network_shares (server, Samba/winbind) |
| workstations (+ operator/coordinator/technician/admin_workstations) | storage, workstation | fpv_network_shares (client, pam_mount), fpv_workstation |
| ground_stations | workstation | fpv_drone_agent |

Проверяется тестом `tests/ansible/test_ansible.py::test_each_device_type_gets_only_its_roles`.

## 7. Секреты

Секреты задаются только в Ansible Vault (`group_vars/<группа>/vault.yml`, `ansible-vault encrypt`):

| Переменная | Назначение |
|---|---|
| `vault_fpv_domain_join_password` | присоединение к домену (передаётся через stdin, `no_log`) |
| `vault_fpv_app_database_password` | пароль роли `fpv_app` в PostgreSQL |
| `vault_fpv_app_ldap_bind_password` | служебная учётная запись LDAP приложения |
| `vault_fpv_agent_key` | ключ устройства для наземной станции |

Пароль sudo — `--ask-become-pass` (интерактивно) или sudo по доменной группе без пароля для
учётной записи автоматизации. Файл пароля Vault — права 600 (проверяется скриптами).
`run-playbook.sh` отклоняет файлы `--extra-vars-file` с секретами в открытом виде.

## 8. Повторный запуск, проверка, откат

```bash
./run-playbook.sh --mode local --inventory inventory/local/hosts.yml --playbook site --check   # без изменений
./postflight.sh --mode local --inventory inventory/local/hosts.yml       # verify.yml + сводка /var/log/fpv/verify-report.json
sudo ./rollback.sh --component application --list
sudo ./rollback.sh --component application --to previous --confirm       # переключение релиза
sudo ./rollback.sh --component config --confirm                           # восстановление копий конфигурации (backup: true)
```

Повторный запуск идемпотентен (в контейнерном тесте второй прогон — `changed=0`).
Разрушительные действия (удаление данных, форматирование дисков, отключение защиты)
ролями не выполняются. Миграции БД не откатываются автоматически: откат приложения допустим,
если схема совместима (миграции только добавляют объекты).

## 9. Запуск для разработки и стенда без домена

```bash
createdb fpv && export FPV_DATABASE_URL=postgresql+psycopg://fpv:fpv@localhost/fpv
export FPV_ENVIRONMENT=development FPV_DIRECTORY_PROVIDER=static FPV_STATIC_DIRECTORY_FILE=/tmp/fpv-dir.json \
       FPV_COOKIE_SECURE=false FPV_VIDEO_SPOOL_DIR=/tmp/fpv-spool FPV_OBJECT_STORE_ROOT=/tmp/fpv-objects \
       FPV_RUN_BACKGROUND_IN_APP=true
cd backend && pip install -r requirements.lock
python -m fpv.migrate && python -m fpv.cli dev-directory --out /tmp/fpv-dir.json   # выводит пароль стенда
psql fpv -f ../database/seeds/dev_devices.sql
uvicorn --factory fpv.main:app_from_env --port 8000
cd ../frontend && npm ci && npm run dev                       # http://127.0.0.1:5173
cd ../drone-agent && FPV_DRONE_KEY=<ключ из кабинета> python -m fpv_agent --server http://127.0.0.1:8000
```

Эмулятор каталога (`static`) запрещён в production проверкой конфигурации.
