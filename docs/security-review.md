# Проверка безопасности (этап 12)

Дата: 12.10.2026. Объём: backend, контур дрона, frontend, Ansible-роли и скрипты, зависимости.

## 1. Проверенные требования ТЗ (разд. 15)

| Требование | Реализация | Проверка |
|---|---|---|
| Доменная аутентификация | LDAPS bind (AD/Samba AD/FreeIPA), эмулятор каталога запрещён в production | `test_auth_domain.py` (в т.ч. LDAP на эмуляторе AD, недоступный сервер, LDAP-инъекция) |
| Пароли не хранятся | нет столбцов паролей; пароль не попадает в БД и журналы | `test_domain_password_never_stored` (поиск по дампу всех таблиц) |
| Авторизация на backend | разрешение из таблицы прав на каждом действии; 404 для чужих объектов | `test_permissions.py` |
| Роли, группы, таблица прав, устройства | `permission_matrix`, сопоставление групп, `device_access_policies` | `test_device_access_policy`, `test_group_change_updates_roles` |
| Отзыв доступа | повторная проверка каталога, синхронизация, отзыв сессий | `test_access_revoked_after_domain_block`, `test_directory_sync_job_revokes_deleted_accounts` |
| Защищённое хранение секретов | файлы `*_FILE` (0640), Ansible Vault, `no_log`, проверка прав файла пароля Vault | `test_secret_tasks_use_no_log`, `test_secrets_not_accepted_in_plain_files`, verify-роль |
| Шифрование при передаче | TLS 1.2+ на nginx, LDAPS/StartTLS, PostgreSQL `hostssl` + SCRAM, SMB3 `smb encrypt = required`, агент — только HTTPS | конфигурация, `test_production_requires_domain_auth_and_tls`, `test_agent_rejects_plain_http_to_remote_server` |
| Проверка входных данных | строгие pydantic-схемы (`extra=forbid`), ограничения длины/диапазонов, проверка размеров видео | `test_unknown_fields_rejected` |
| Ограничение частоты | в приложении (вход, изменения, контур дрона) и nginx `limit_req` | `test_login_rate_limit` |
| Защита от повторной отправки | `Idempotency-Key`, `expected_version`, уникальные индексы | `test_idempotent_order_creation_after_reconnect`, `test_version_conflict`, `test_points_awarded_once` |
| Журналирование админ-действий | неизменяемый аудит с хеш-цепочкой | `test_audit.py` (в т.ч. правка суперпользователем обнаруживается) |
| Резервное копирование | ежедневный `pg_dump` с проверкой восстановления | роль `fpv_postgresql` |
| Мониторинг ошибок и доступности | `/health/ready`, `fpv-healthcheck`, node exporter, страница мониторинга, dead-очередь outbox | `test_outbox_dead_letter_after_max_attempts`, `test_database_outage_reported_by_readiness` |
| Управление сессиями | серверные сессии, тайм-аут простоя/срок, отзыв, cookie HttpOnly/Secure/SameSite | `test_session_idle_timeout`, `test_logout_revokes_session` |
| Видеоподтверждения | SHA-256, запрет перезаписи/удаления, проверка целостности, журнал просмотров | `test_video.py` |
| Раздельные контуры телеметрии и управления | `/drone-link` с ключом устройства; команд полёта нет | `test_adapter_interface_has_no_flight_commands` |

## 2. Анализ зависимостей (pip-audit, npm audit)

Исправлено в ходе проверки:

* React Router 6.28.0 → **6.30.6** (GHSA-2w69-qvjg-hvjx, GHSA-2j2x-hqr9-3h42 — open redirect/XSS);
* FastAPI 0.115.6 / Starlette 0.41.3 → **0.128.8 / 0.49.3** (часть уязвимостей Starlette);
* cryptography 47.0.0 → **50.0.2** (Ansible-окружение);
* boto3 вынесен в необязательные зависимости (`requirements-s3.lock`): на Python 3.9 он требует
  urllib3 1.26 с известными уязвимостями; при `FPV_OBJECT_STORE=filesystem` не устанавливается.

**Не устраняются на Python 3.9** (исправленные версии требуют Python ≥ 3.10, см. `debian11-lifecycle.md`):

| Пакет | Идентификатор | Суть | Применимость | Мера |
|---|---|---|---|---|
| anyio 4.12.1 | CVE-2026-63374 | TLS с IDN-именами в `connect_tcp`/`TLSStream` | не применяется: приложение не открывает TLS через anyio (БД — psycopg, LDAP — ldap3) | — |
| anyio 4.12.1 | CVE-2026-64847 | не вычитывается stderr процессов-воркеров | не применяется: пул процессов anyio не используется | — |
| starlette 0.49.3 | CVE-2026-48710, CVE-2026-54282 | `request.url` строится из непроверенного Host/пути | приложение не использует `request.url` для перенаправлений и проверок | nginx `default_server` отбрасывает неизвестные Host |
| starlette 0.49.3 | CVE-2026-54283 | лимиты `request.form()` для urlencoded | не применяется: форм нет, только JSON | — |
| starlette 0.49.3 | CVE-2026-48818 | SSRF в `StaticFiles` на Windows | не применяется (Linux, `StaticFiles` не используется) | — |
| starlette 0.49.3 | CVE-2026-48817 | выбор метода в `HTTPEndpoint` через getattr | не применяется (`HTTPEndpoint` не используется) | — |
| ansible-core 2.15.13 | CVE-2024-8775 | секреты из Vault при `include_vars` без `no_log` | `include_vars` не используется; задачи с секретами — `no_log` (тест) | — |
| ansible-core 2.15.13 | CVE-2024-11079 | обход защиты unsafe через `hostvars` | данные с узлов не подставляются в шаблоны | управляющий узел — только доверенные администраторы |
| ansible-core 2.15.13 | CVE-2026-11332 | инъекция параметров git в `ansible-galaxy role install` | роли из Galaxy не устанавливаются; коллекции — закреплённые/офлайн | — |

Повтор проверки: `uvx pip-audit -r backend/requirements.lock --require-hashes`,
`uvx pip-audit -r deploy/ansible/requirements.txt --require-hashes`, `npm audit --omit=dev`.

## 3. Найдено и исправлено при разработке

* Приоритет переменных Ansible: значения группы `all` в файле inventory перекрывались
  `playbooks/group_vars/all.yml`, из-за чего staging получал `fpv_environment: production`.
  Исправлено: окружение — в `inventory/<env>/group_vars/all.yml`, флаги CI — на уровне узла;
  `site.yml` требует явного `fpv_environment`; добавлен тест `test_inventory_overrides_take_effect`.
* Счётчик дубликатов телеметрии при пакетной вставке (rowcount = −1) — переход на `RETURNING`.
* `cryptography` ≥ 44 не устанавливается на Python 3.9.0/3.9.1: загрузка wheels явно для 3.9.2 (Debian 11).

## 4. Остаточные риски и рекомендации

1. Обновление ОС/СУБД/Python — главный риск (см. `debian11-lifecycle.md`).
2. Ограничение частоты в приложении работает в пределах процесса; основной барьер — nginx.
3. Открытый WebSocket продлевает сессию (проверка раз в 60 с) — допустимо для рабочих мест в ЦОД;
   при необходимости уменьшить `FPV_SESSION_TTL_S`.
4. MP4-копия видео создаётся ffmpeg из данных устройства: запуск с тайм-аутом, в песочнице systemd
   (`ProtectSystem=strict`, `NoNewPrivileges`); при повышенных требованиях — отдельный узел транскодирования.
5. Буфер видео на диске сервера приложений — использовать шифрование тома (LUKS).
6. SSH: `PasswordAuthentication yes` оставлен для доменных пользователей; рекомендуется перейти на
   Kerberos/GSSAPI и ключи, затем отключить пароли.
7. Вход по Kerberos SSO в браузере (SPNEGO) не реализован.
8. Тело видеосегмента без `Content-Length` (chunked) ограничивается nginx `client_max_body_size`
   (10 МБ) и проверкой размера после чтения.
