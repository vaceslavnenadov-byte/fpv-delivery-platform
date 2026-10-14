# Пакеты Debian 13 и локальный репозиторий apt

Платформа поставляется двумя пакетами:

| Пакет | Архитектура | Содержимое |
|---|---|---|
| `fpv-platform` | amd64 | backend, воркер, SQL-миграции, веб-интерфейс, службы `fpv-api`/`fpv-worker`, wheel-файлы всех Python-зависимостей с хешами |
| `fpv-drone-agent` | all | сервисный модуль дрона (симулятор) и служба `fpv-agent` |

Архитектура `amd64`: среди зависимостей есть скомпилированные модули (psycopg, pydantic-core).
Виртуальное окружение `/usr/lib/fpv-platform/venv` создаётся в `postinst` из вложенных
wheel-файлов (`pip --no-index --require-hashes`): сеть при установке не нужна.

## Сборка (на машине сборки Debian 13)

```bash
sudo apt install -y dpkg-dev gnupg nodejs npm
git clone https://github.com/vaceslavnenadov-byte/fpv-delivery-platform
git clone https://github.com/vaceslavnenadov-byte/fpv-delivery-packages
(cd fpv-delivery-packages && scripts/fetch-wheels.sh)          # wheels для Python 3.13 / x86_64
cd fpv-delivery-platform
packaging/debian/build-debs.sh --build-frontend                # → build/debs/*.deb + SHA256SUMS-<версия>
```

Новая сборка той же версии кода — `--revision 2` и т.д. (версия пакета `0.1.0-2`).

## Локальный репозиторий в /srv/packages

```bash
gpg --quick-gen-key "FPV Local Repo" rsa4096 sign 3y                 # один раз
sudo GNUPGHOME=$HOME/.gnupg packaging/debian/make-apt-repo.sh /srv/packages --key "FPV Local Repo" build/debs/*.deb
```

Скрипт формирует `Packages`, `Release`, подписи `InRelease`/`Release.gpg` и открытый ключ
`/srv/packages/fpv-local.gpg`. Повторный запуск с новыми `.deb` обновляет индекс.

## Клиент

```bash
sudo install -m 0644 /srv/packages/fpv-local.gpg /usr/share/keyrings/fpv-local.gpg
sudo cp packaging/debian/fpv-local.sources /etc/apt/sources.list.d/fpv-local.sources
sudo apt update
sudo apt install fpv-platform          # сервер приложений
sudo apt install fpv-drone-agent       # наземная станция
```

`/etc/apt/sources.list.d/fpv-local.sources`:

```
Types: deb
URIs: file:/srv/packages
Suites: ./
Signed-By: /usr/share/keyrings/fpv-local.gpg
```

Для других узлов каталог публикуется по HTTPS (nginx, `autoindex off`) и в `URIs`
указывается `https://repo.dc.fpv.local/fpv`.

## Настройка после установки

1. `/etc/fpv/secrets/database_url` — строка подключения к PostgreSQL 17 (файл-заготовка `root:fpv 0640`).
2. `/etc/fpv/secrets/ldap_bind_password` и параметры `FPV_LDAP_*` в `/etc/fpv/fpv.env`.
3. `sudo dpkg-reconfigure fpv-platform` — миграции БД и запуск `fpv-api`, `fpv-worker`.
4. nginx: `ln -s /etc/nginx/sites-available/fpv-platform.conf /etc/nginx/sites-enabled/`, имя сервера и сертификат.
5. Сервисный модуль: ключ устройства в `/etc/fpv-agent/key` (`fpv-agent`, 0600), адрес сервера в
   `/etc/default/fpv-agent`, затем `systemctl start fpv-agent`.

Обновление — `apt install --only-upgrade fpv-platform` (venv пересоздаётся, миграции применяются
автоматически; отключается `FPV_AUTO_MIGRATE=no` в `/etc/default/fpv-platform`). Ошибка миграции
останавливает настройку пакета; после исправления — `dpkg --configure -a`.

Удаление (`remove`/`purge`) не затрагивает данные: `/var/lib/fpv`, `/srv/fpv-evidence`,
`/var/log/fpv`, `/etc/fpv/secrets` и учётная запись `fpv` сохраняются.

## Пакеты и Ansible

Пакеты — альтернатива роли `fpv_application` для узлов, где ПО ставится через apt. Роли
`fpv_base`, `fpv_domain`, `fpv_postgresql`, `fpv_network_shares` и другие применяются как прежде.

## Проверки

`tests/packaging/test_debs.py`: состав и права файлов, полнота wheel-файлов по lock-файлу,
сценарии сопровождающего, индекс репозитория. С `FPV_TEST_DOCKER=1` и
`FPV_TEST_DEB_DATABASE_URL` — полный цикл в контейнере Debian 13: подписанный репозиторий
`/srv/packages` → `apt install` → миграции PostgreSQL 17 → запуск API → обновление до новой
ревизии → `remove`/`purge` с сохранением данных. Отдельно проверено, что apt отвергает
изменённый без переподписи индекс.

Ограничение проверки: в контейнере не было systemd и интерпретатора Python из пакета Debian
(зависимости `python3`/`python3-venv` подменялись тестовыми пакетами-заглушками).
