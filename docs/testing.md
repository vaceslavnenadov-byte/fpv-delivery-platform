# Тестирование

## Запуск

```bash
# backend + сервисный модуль дрона: реальный PostgreSQL 13+ (роль с CREATEDB)
cd fpv-delivery-platform
python3.9 -m venv .venv && .venv/bin/pip install --require-hashes -r backend/requirements.lock -r backend/requirements-dev.lock
FPV_TEST_PG=postgresql+psycopg://fpv:fpv@127.0.0.1:5432 \
FPV_TEST_PG_SUPERUSER=postgresql+psycopg://postgres:***@127.0.0.1:5432 \   # необязательно: тест подмены аудита
  .venv/bin/python -m pytest tests/backend

# Ansible: venv с ansible-core 2.15.13 и pytest; FPV_TEST_DOCKER=1 — офлайн-установка в контейнере Debian 11
FPV_ANSIBLE_VENV=/path/to/venv FPV_PACKAGES_DIR=../fpv-delivery-packages FPV_TEST_DOCKER=1 \
  /path/to/venv/bin/python -m pytest tests/ansible

# frontend
cd frontend && npm ci && npm run build
```

## Результаты последнего прогона

| Набор | Окружение | Результат |
|---|---|---|
| `tests/backend` (107 тестов) | Python 3.9 + PostgreSQL 13.21 | все пройдены |
| `tests/backend` | Python 3.9 + PostgreSQL 16 | все пройдены |
| `tests/backend` | Python 3.13 + PostgreSQL 13.21 | все пройдены (проверка плана обновления) |
| `tests/ansible` (40 тестов) | ansible-core 2.15.13, shellcheck, Docker `python:3.9-slim-bullseye`, `--network none` | все пройдены |
| frontend | TypeScript strict, Vite build | без ошибок |
| сквозной UI-сценарий | Chromium (Playwright), все роли, симулятор с реальным MPEG-TS | пройден, ошибок в консоли нет |

## Соответствие разделу 17 ТЗ

| Требование | Тесты |
|---|---|
| доменная аутентификация и недоступность службы | `test_auth_domain.py`: `test_domain_login_maps_groups_to_roles`, `test_directory_unavailable_returns_503`, `test_existing_session_survives_short_directory_outage`, LDAP: `test_ldap_provider_*`, `test_ldap_unreachable_server_is_unavailable` |
| сопоставление доменных пользователей и групп | `test_group_change_updates_roles`, `test_login_variants_of_username`, `test_user_without_application_role_denied` |
| таблица прав для пользователей и устройств | `test_permissions.py`, `test_device_access_policy`, `test_matrix_exposed_to_admin` |
| отзыв доступа после блокировки | `test_access_revoked_after_domain_block`, `test_directory_sync_job_revokes_deleted_accounts`, `test_admin_disable_user_revokes_sessions` |
| персональные и общие сетевые папки | `tests/ansible/test_shares_policy.py` (матрица, smb.conf, pam_mount, запрет по умолчанию) |
| регистрация и авторизация | `test_auth_domain.py`, `test_csrf_required_for_mutations`, `test_logout_revokes_session` |
| роли и разрешения | `test_permissions.py` |
| создание заказа | `test_orders.py`: проверки груза/зоны/окна/дрона, идемпотентность |
| назначение оператора | `test_concurrent_accept_only_one_wins`, `test_operator_decline_hides_order_and_release_returns_to_queue`, `test_coordinator_has_no_operator_rights` |
| проверка статусов | `test_invalid_transitions_rejected`, `test_full_lifecycle_with_video_and_scoring` |
| симуляция выполнения | `test_drone_agent.py::test_agent_full_mission_with_simulator` |
| сохранение последних 15 секунд | `test_clip_contains_last_15_seconds_before_completion`, `test_ring_buffer_keeps_only_recent_segments`, `test_real_mpegts_clip_gets_mp4_preview` |
| подтверждение администратором | `test_full_lifecycle_*`, `test_admin_rejects_delivery` |
| начисление и повторная обработка баллов | `test_points_awarded_once`, `test_preliminary_then_final`, `test_dispute_*`, `test_operator_appeal`, `test_handler_side_effects_are_idempotent` |
| потеря связи | `test_link_loss_warning_then_safe_abort`, `test_link_restored_before_threshold`, `test_agent_buffers_telemetry_during_link_loss` |
| отказ сервера | `test_storage_outage_retries_then_recovers`, `test_database_outage_reported_by_readiness`, `test_outbox_dead_letter_after_max_attempts` |
| восстановление после сбоя | `test_crash_after_capture_before_upload`, `test_agent_sequence_survives_restart` |
| несанкционированное изменение данных | `test_unknown_fields_rejected`, `test_customer_cannot_see_foreign_order`, `test_evidence_rows_cannot_be_modified_or_deleted`, `test_tampered_file_detected` |
| журнал аудита | `test_audit.py` (полнота, только добавление, обнаружение подмены, маскирование секретов) |
| видео отсутствует | `test_missing_video_is_recorded_not_faked`, `test_stale_stream_before_event_marked_missing`, `test_cannot_approve_missing_video` |
| заказ отменён | `test_customer_cancel_before_start`, `test_cancelled_order_gets_no_points` |
| дрон неисправен | `test_faulty_drone_not_admitted`, `test_drone_fault_between_accept_and_start`, `test_critical_drone_error_aborts_order`, `test_faulty_simulator_reports_self_check` |
| администратор отклоняет подтверждение | `test_admin_rejects_delivery`, `test_reject_requires_reason` |
| Ansible (разд. 19.7) | `tests/ansible/test_ansible.py`: синтаксис, роли по типам устройств, venv, коды завершения скриптов, идемпотентность, остановка при ошибке, отсутствие секретов |

## Что не проверено автоматически

* Вход через реальный AD/Samba AD DC и присоединение узлов к домену (роль `fpv_domain`) — в среде
  разработки не было контроллера домена; LDAP-провайдер проверен на эмуляторе ldap3.
* Роли с установкой пакетов и службами (`fpv_postgresql`, Samba, nginx, SSSD) на реальном Debian 11 —
  в среде не было доступа к зеркалу Debian; проверены синтаксис, шаблоны и `--check`-совместимость,
  полный прогон — в контейнере без пакетов/systemd (`inventory/ci`).
* Совместимость манифеста пакетов с зеркалом (`fpv-delivery-packages/scripts/check-debian11.sh`).
* S3-хранилище (`S3ObjectStore`) — нет S3-совместимой службы в среде; по умолчанию используется ФС.
