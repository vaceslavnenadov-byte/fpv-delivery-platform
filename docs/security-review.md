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

Результат на 13.10.2026 после перехода на Debian 13 (Python 3.13):

| Набор | Ключевые версии | Результат |
|---|---|---|
| `backend/requirements.lock` | FastAPI 0.143.0, Starlette 1.7.0, anyio 4.15.1, pydantic 2.14.1, SQLAlchemy 2.0.54, psycopg 3.2.13, uvicorn 0.54.0 | известных уязвимостей нет |
| `backend/requirements-s3.lock` | boto3 1.43, urllib3 2.8.0 | известных уязвимостей нет |
| `deploy/ansible/requirements.txt` | ansible-core 2.21.5, cryptography 50.0.2, jinja2 3.1.6 | известных уязвимостей нет |
| `frontend` (production) | React 18.3.1, React Router 7.18.4 | известных уязвимостей нет |

История: на Debian 11 (Python 3.9) оставались неустранимые записи по anyio, Starlette и
ansible-core 2.15 — исправленные версии требуют Python ≥ 3.10. Переход на Debian 13 их устранил.
React Router обновлён 6.28.0 → 6.30.6 → **7.18.4** (open redirect/XSS, в т.ч. GHSA-wrjc-x8rr-h8h6 —
обход исправления CVE-2025-68470, для ветки 6.x исправления нет); используется декларативный режим.

Замечание по сопровождению: `ldap3` 2.9.1 (последний выпуск — 2021 г.) использует устаревший API
`pyasn1` (предупреждения `DeprecationWarning`). Уязвимостей нет, но при следующем обновлении
`pyasn1` возможна несовместимость — рассмотреть переход на `python-ldap` (пакет `python3-ldap`
в Debian 13) за интерфейсом `LdapDirectoryProvider`.

Повтор проверки: `uvx pip-audit -r backend/requirements.lock --require-hashes`,
`uvx pip-audit -r deploy/ansible/requirements.txt --require-hashes`, `npm audit --omit=dev`.

## 3. Найдено и исправлено при разработке

* Приоритет переменных Ansible: значения группы `all` в файле inventory перекрывались
  `playbooks/group_vars/all.yml`, из-за чего staging получал `fpv_environment: production`.
  Исправлено: окружение — в `inventory/<env>/group_vars/all.yml`, флаги CI — на уровне узла;
  `site.yml` требует явного `fpv_environment`; добавлен тест `test_inventory_overrides_take_effect`.
* Счётчик дубликатов телеметрии при пакетной вставке (rowcount = −1) — переход на `RETURNING`.
* ansible-core ≥ 2.19 требует библиотеку `packaging` на целевом узле для модуля `pip`: добавлен
  пакет `python3-packaging` на серверах приложений (выявлено контейнерным тестом на Debian 13).

## 4. Остаточные риски и рекомендации

1. Регулярное обновление ОС и зависимостей (см. `os-lifecycle.md`); `ldap3` — кандидат на замену.
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
