-- 0001_core_schema.sql
-- Базовая схема платформы гражданской доставки FPV.
-- Целевая СУБД: PostgreSQL 13 (Debian 11). Совместимо с PostgreSQL 13-16.
--
-- Правила удаления данных:
--   * пользователи никогда не удаляются физически (status = 'deleted' + обезличивание);
--   * заказы, история статусов, начисления баллов, аудит и журналы просмотра видео
--     ссылаются на пользователей через ON DELETE RESTRICT и защищены триггерами
--     от изменения/удаления;
--   * телеметрия и видеоклипы удаляются только процедурами хранения (retention).

-- gen_random_uuid() входит в ядро PostgreSQL начиная с версии 13, расширения не требуются.

-- ---------------------------------------------------------------------------
-- Роли, разрешения, таблица прав
-- ---------------------------------------------------------------------------
CREATE TABLE roles (
    id          smallserial PRIMARY KEY,
    code        text NOT NULL UNIQUE CHECK (code ~ '^[a-z_]{2,32}$'),
    name        text NOT NULL,
    description text NOT NULL DEFAULT ''
);

CREATE TABLE permissions (
    code        text PRIMARY KEY CHECK (code ~ '^[a-z_.]{3,64}$'),
    description text NOT NULL
);

CREATE TABLE permission_matrix (
    role_id    smallint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission text     NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission)
);

-- ---------------------------------------------------------------------------
-- Пользователи (привязка к доменной учётной записи, без паролей)
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_object_id       text NOT NULL UNIQUE,      -- objectGUID (AD) / ipaUniqueID (FreeIPA)
    domain_username        text NOT NULL UNIQUE,      -- sAMAccountName / uid, в нижнем регистре
    domain_upn             text,
    display_name           text NOT NULL,
    email                  text,
    phone                  text,
    status                 text NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'disabled', 'deleted')),
    directory_enabled      boolean NOT NULL DEFAULT true,
    profile_completed      boolean NOT NULL DEFAULT false,
    last_login_at          timestamptz,
    last_directory_sync_at timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    deleted_at             timestamptz
);

CREATE TABLE user_roles (
    user_id    uuid     NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role_id    smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    source     text     NOT NULL CHECK (source IN ('domain', 'manual')),
    granted_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id, source)
);

CREATE TABLE domain_groups (
    id          serial PRIMARY KEY,
    dn          text NOT NULL UNIQUE,
    name        text NOT NULL UNIQUE,             -- короткое имя группы (sAMAccountName / cn)
    role_id     smallint REFERENCES roles(id) ON DELETE SET NULL,  -- сопоставление с ролью приложения
    description text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_domain_groups (
    user_id         uuid    NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    domain_group_id integer NOT NULL REFERENCES domain_groups(id) ON DELETE CASCADE,
    synced_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, domain_group_id)
);

-- ---------------------------------------------------------------------------
-- Устройства сети и политики доступа к ним
-- ---------------------------------------------------------------------------
CREATE TABLE devices (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code          text NOT NULL UNIQUE CHECK (code ~ '^[A-Za-z0-9._-]{2,64}$'),
    kind          text NOT NULL CHECK (kind IN ('workstation', 'server', 'ground_station', 'mobile', 'other')),
    hostname      text,
    description   text NOT NULL DEFAULT '',
    domain_joined boolean NOT NULL DEFAULT false,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'blocked', 'retired')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE device_access_policies (
    id              serial PRIMARY KEY,
    device_id       uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    role_id         smallint REFERENCES roles(id) ON DELETE CASCADE,
    domain_group_id integer  REFERENCES domain_groups(id) ON DELETE CASCADE,
    access_level    text NOT NULL DEFAULT 'use' CHECK (access_level IN ('use', 'admin')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((role_id IS NOT NULL) <> (domain_group_id IS NOT NULL))
);
CREATE UNIQUE INDEX device_access_policies_role_uq  ON device_access_policies (device_id, role_id) WHERE role_id IS NOT NULL;
CREATE UNIQUE INDEX device_access_policies_group_uq ON device_access_policies (device_id, domain_group_id) WHERE domain_group_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Сессии (токены хранятся только в виде SHA-256)
-- ---------------------------------------------------------------------------
CREATE TABLE sessions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    token_hash             bytea NOT NULL UNIQUE,
    csrf_hash              bytea NOT NULL,
    device_id              uuid REFERENCES devices(id) ON DELETE SET NULL,
    ip_address             inet,
    user_agent             text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    expires_at             timestamptz NOT NULL,
    last_seen_at           timestamptz NOT NULL DEFAULT now(),
    last_directory_check_at timestamptz NOT NULL DEFAULT now(),
    revoked_at             timestamptz,
    revoke_reason          text
);
CREATE INDEX sessions_user_active_idx ON sessions (user_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- Операторы
-- ---------------------------------------------------------------------------
CREATE TABLE operators (
    user_id        uuid PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    license_number text NOT NULL,
    notes          text NOT NULL DEFAULT '',
    decided_by     uuid REFERENCES users(id) ON DELETE RESTRICT,
    decided_at     timestamptz,
    total_points   integer NOT NULL DEFAULT 0,
    completed_orders integer NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Дроны, проверки состояния, обслуживание
-- ---------------------------------------------------------------------------
CREATE TABLE drones (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    serial_number       text NOT NULL UNIQUE,
    registration_number text NOT NULL UNIQUE,     -- учётный номер БВС гражданского назначения
    manufacturer        text NOT NULL,
    model               text NOT NULL,
    adapter_kind        text NOT NULL DEFAULT 'simulator',  -- тип адаптера интеграции
    max_payload_g       integer NOT NULL CHECK (max_payload_g > 0),
    max_range_m         integer NOT NULL CHECK (max_range_m > 0),
    status              text NOT NULL DEFAULT 'pending_approval'
                        CHECK (status IN ('pending_approval', 'active', 'blocked', 'retired')),
    health_status       text NOT NULL DEFAULT 'requires_check'
                        CHECK (health_status IN ('serviceable', 'requires_check', 'grounded')),
    link_status         text NOT NULL DEFAULT 'offline'
                        CHECK (link_status IN ('online', 'degraded', 'lost', 'offline')),
    last_seen_at        timestamptz,
    battery_pct         smallint CHECK (battery_pct BETWEEN 0 AND 100),
    firmware_version    text,
    api_key_hash        bytea UNIQUE,
    api_key_prefix      text,
    api_key_issued_at   timestamptz,
    blocked_reason      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX drones_owner_idx ON drones (owner_id);

CREATE TABLE drone_health_checks (
    id          bigserial PRIMARY KEY,
    drone_id    uuid NOT NULL REFERENCES drones(id) ON DELETE RESTRICT,
    source      text NOT NULL CHECK (source IN ('device', 'technician', 'system')),
    result      text NOT NULL CHECK (result IN ('pass', 'warn', 'fail')),
    battery_pct smallint CHECK (battery_pct BETWEEN 0 AND 100),
    error_codes text[] NOT NULL DEFAULT '{}',
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by  uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX drone_health_checks_drone_idx ON drone_health_checks (drone_id, created_at DESC);

CREATE TABLE maintenance_records (
    id            bigserial PRIMARY KEY,
    drone_id      uuid NOT NULL REFERENCES drones(id) ON DELETE RESTRICT,
    technician_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind          text NOT NULL CHECK (kind IN ('inspection', 'repair', 'component_replacement', 'firmware_update')),
    description   text NOT NULL,
    components    jsonb NOT NULL DEFAULT '[]'::jsonb,
    health_status_set text CHECK (health_status_set IN ('serviceable', 'requires_check', 'grounded')),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX maintenance_records_drone_idx ON maintenance_records (drone_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Заказы
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    number            bigserial UNIQUE,
    customer_id       uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    drone_id          uuid NOT NULL REFERENCES drones(id) ON DELETE RESTRICT,
    operator_id       uuid REFERENCES users(id) ON DELETE RESTRICT,
    coordinator_id    uuid REFERENCES users(id) ON DELETE RESTRICT,
    status            text NOT NULL DEFAULT 'draft' CHECK (status IN (
                          'draft', 'pending_confirmation', 'awaiting_operator', 'accepted',
                          'awaiting_readiness', 'ready', 'in_progress',
                          'awaiting_delivery_confirmation', 'admin_review', 'completed',
                          'rejected', 'cancelled', 'aborted_technical', 'disputed')),
    status_before_dispute text,
    cargo_type        text NOT NULL,
    cargo_description text NOT NULL,
    cargo_weight_g    integer NOT NULL CHECK (cargo_weight_g > 0),
    cargo_length_mm   integer NOT NULL CHECK (cargo_length_mm > 0),
    cargo_width_mm    integer NOT NULL CHECK (cargo_width_mm > 0),
    cargo_height_mm   integer NOT NULL CHECK (cargo_height_mm > 0),
    recipient_name    text NOT NULL,
    recipient_phone   text NOT NULL,
    window_start      timestamptz NOT NULL,
    window_end        timestamptz NOT NULL,
    quote_amount      numeric(12, 2),
    quote_currency    text NOT NULL DEFAULT 'RUB',
    validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    receipt_overdue   boolean NOT NULL DEFAULT false,
    version           integer NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    submitted_at      timestamptz,
    accepted_at       timestamptz,
    started_at        timestamptz,
    delivered_at      timestamptz,     -- событие завершения доставки
    completed_at      timestamptz,
    closed_reason     text,
    CHECK (window_end > window_start)
);
CREATE INDEX orders_status_idx      ON orders (status);
CREATE INDEX orders_customer_idx    ON orders (customer_id, created_at DESC);
CREATE INDEX orders_operator_idx    ON orders (operator_id) WHERE operator_id IS NOT NULL;
CREATE INDEX orders_coordinator_idx ON orders (coordinator_id) WHERE coordinator_id IS NOT NULL;
-- Один дрон не может выполнять два активных заказа одновременно.
CREATE UNIQUE INDEX orders_drone_active_uq ON orders (drone_id)
    WHERE status IN ('accepted', 'awaiting_readiness', 'ready', 'in_progress');

CREATE TABLE order_status_history (
    id          bigserial PRIMARY KEY,
    order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    from_status text,
    to_status   text NOT NULL,
    actor_id    uuid REFERENCES users(id) ON DELETE RESTRICT,
    actor_kind  text NOT NULL CHECK (actor_kind IN ('user', 'system', 'drone')),
    reason      text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX order_status_history_order_idx ON order_status_history (order_id, id);

CREATE TABLE routes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        uuid NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    origin_lat      double precision NOT NULL CHECK (origin_lat BETWEEN -90 AND 90),
    origin_lon      double precision NOT NULL CHECK (origin_lon BETWEEN -180 AND 180),
    origin_address  text NOT NULL,
    dest_lat        double precision NOT NULL CHECK (dest_lat BETWEEN -90 AND 90),
    dest_lon        double precision NOT NULL CHECK (dest_lon BETWEEN -180 AND 180),
    dest_address    text NOT NULL,
    distance_m      integer NOT NULL CHECK (distance_m >= 0),
    max_altitude_m  integer NOT NULL DEFAULT 120,
    checkpoints     jsonb NOT NULL DEFAULT '[]'::jsonb,
    approval_status text NOT NULL DEFAULT 'pending'
                    CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    approval_notes  jsonb NOT NULL DEFAULT '[]'::jsonb,
    approved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE assignments (
    id         bigserial PRIMARY KEY,
    order_id   uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role       text NOT NULL CHECK (role IN ('operator', 'coordinator')),
    status     text NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'declined', 'released', 'finished')),
    reason     text NOT NULL DEFAULT '',
    created_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    ended_at   timestamptz
);
CREATE UNIQUE INDEX assignments_active_uq ON assignments (order_id, role) WHERE status = 'active';
CREATE INDEX assignments_user_idx ON assignments (user_id, status);

CREATE TABLE order_notes (
    id         bigserial PRIMARY KEY,
    order_id   uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    author_id  uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind       text NOT NULL CHECK (kind IN ('remark', 'message_to_operator', 'incident', 'deviation')),
    severity   text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    body       text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX order_notes_order_idx ON order_notes (order_id, id);

-- ---------------------------------------------------------------------------
-- Телеметрия
-- ---------------------------------------------------------------------------
CREATE TABLE telemetry_records (
    id           bigserial PRIMARY KEY,
    drone_id     uuid NOT NULL REFERENCES drones(id) ON DELETE RESTRICT,
    order_id     uuid REFERENCES orders(id) ON DELETE RESTRICT,
    seq          bigint NOT NULL,
    recorded_at  timestamptz NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    lat          double precision CHECK (lat BETWEEN -90 AND 90),
    lon          double precision CHECK (lon BETWEEN -180 AND 180),
    alt_m        real,
    ground_speed_mps real,
    heading_deg  real,
    battery_pct  smallint CHECK (battery_pct BETWEEN 0 AND 100),
    link_quality smallint CHECK (link_quality BETWEEN 0 AND 100),
    gnss_fix     boolean,
    flight_phase text,
    extra        jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (drone_id, seq)
);
CREATE INDEX telemetry_order_idx ON telemetry_records (order_id, recorded_at) WHERE order_id IS NOT NULL;
CREATE INDEX telemetry_drone_idx ON telemetry_records (drone_id, recorded_at DESC);

-- ---------------------------------------------------------------------------
-- Видеоподтверждения
-- ---------------------------------------------------------------------------
CREATE TABLE video_evidence (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id             uuid NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    status               text NOT NULL CHECK (status IN ('pending_upload', 'stored', 'missing', 'corrupted', 'purged')),
    missing_reason       text,
    object_key           text,
    preview_object_key   text,
    sha256               text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes           bigint,
    content_type         text,
    completion_event_at  timestamptz NOT NULL,
    clip_start_at        timestamptz,
    clip_end_at          timestamptz,
    segment_count        integer NOT NULL DEFAULT 0,
    retention_until      timestamptz NOT NULL,
    review_status        text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewed_by          uuid REFERENCES users(id) ON DELETE RESTRICT,
    reviewed_at          timestamptz,
    review_comment       text,
    integrity_checked_at timestamptz,
    integrity_ok         boolean,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'stored' OR (object_key IS NOT NULL AND sha256 IS NOT NULL)),
    CHECK (status <> 'missing' OR missing_reason IS NOT NULL)
);

CREATE TABLE video_access_log (
    id         bigserial PRIMARY KEY,
    video_id   uuid NOT NULL REFERENCES video_evidence(id) ON DELETE RESTRICT,
    user_id    uuid REFERENCES users(id) ON DELETE RESTRICT,
    action     text NOT NULL CHECK (action IN ('view', 'download', 'integrity_check', 'review')),
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Подтверждения доставки, баллы, жалобы
-- ---------------------------------------------------------------------------
CREATE TABLE delivery_confirmations (
    id         bigserial PRIMARY KEY,
    order_id   uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    kind       text NOT NULL CHECK (kind IN ('operator_report', 'recipient_receipt', 'admin_review')),
    actor_id   uuid REFERENCES users(id) ON DELETE RESTRICT,
    decision   text NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'submitted')),
    comment    text NOT NULL DEFAULT '',
    report     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_id, kind)
);

CREATE TABLE scoring_rules (
    version    serial PRIMARY KEY,
    rules      jsonb NOT NULL,
    is_active  boolean NOT NULL DEFAULT false,
    comment    text NOT NULL DEFAULT '',
    created_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX scoring_rules_one_active ON scoring_rules (is_active) WHERE is_active;

CREATE TABLE score_transactions (
    id           bigserial PRIMARY KEY,
    operator_id  uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    order_id     uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    kind         text NOT NULL CHECK (kind IN ('final', 'adjustment')),
    points       integer NOT NULL CHECK (points BETWEEN -100 AND 100),
    breakdown    jsonb NOT NULL DEFAULT '{}'::jsonb,
    rule_version integer REFERENCES scoring_rules(version) ON DELETE RESTRICT,
    reason       text NOT NULL DEFAULT '',
    complaint_id bigint,
    created_by   uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'adjustment' OR length(reason) > 0)
);
-- Окончательное начисление по заказу возможно только один раз.
CREATE UNIQUE INDEX score_transactions_final_uq ON score_transactions (order_id) WHERE kind = 'final';
CREATE INDEX score_transactions_operator_idx ON score_transactions (operator_id, created_at DESC);

CREATE TABLE complaints (
    id            bigserial PRIMARY KEY,
    order_id      uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    author_id     uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind          text NOT NULL CHECK (kind IN ('dispute', 'score_appeal')),
    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'upheld', 'dismissed')),
    description   text NOT NULL CHECK (length(description) BETWEEN 5 AND 4000),
    resolution    text,
    points_adjustment integer,
    resolved_by   uuid REFERENCES users(id) ON DELETE RESTRICT,
    resolved_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX complaints_open_uq ON complaints (order_id, kind) WHERE status = 'open';
ALTER TABLE score_transactions
    ADD CONSTRAINT score_transactions_complaint_fk FOREIGN KEY (complaint_id) REFERENCES complaints(id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX score_transactions_complaint_uq ON score_transactions (complaint_id) WHERE complaint_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Уведомления, аудит, очередь событий, идемпотентность
-- ---------------------------------------------------------------------------
CREATE TABLE notifications (
    id         bigserial PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind       text NOT NULL,
    severity   text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    title      text NOT NULL,
    body       text NOT NULL DEFAULT '',
    order_id   uuid REFERENCES orders(id) ON DELETE RESTRICT,
    dedup_key  text,
    read_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications (user_id, id DESC);
CREATE UNIQUE INDEX notifications_dedup_uq ON notifications (user_id, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE audit_logs (
    id          bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id    uuid REFERENCES users(id) ON DELETE RESTRICT,
    actor_label text NOT NULL,            -- имя пользователя / 'system' / 'drone:<serial>'
    actor_ip    inet,
    action      text NOT NULL,
    entity_type text NOT NULL DEFAULT '',
    entity_id   text NOT NULL DEFAULT '',
    outcome     text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success', 'denied', 'failure')),
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash   text NOT NULL,
    hash        text NOT NULL UNIQUE
);
CREATE INDEX audit_logs_entity_idx ON audit_logs (entity_type, entity_id);
CREATE INDEX audit_logs_actor_idx  ON audit_logs (actor_id, id DESC);
CREATE INDEX audit_logs_action_idx ON audit_logs (action, id DESC);

CREATE TABLE outbox_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           text NOT NULL,
    payload         jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'dead')),
    attempts        integer NOT NULL DEFAULT 0,
    max_attempts    integer NOT NULL DEFAULT 8,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_until    timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz
);
CREATE INDEX outbox_pending_idx ON outbox_events (next_attempt_at) WHERE status IN ('pending', 'processing');

CREATE TABLE processed_events (
    event_id     uuid NOT NULL,
    handler      text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, handler)
);

CREATE TABLE idempotency_keys (
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    key             text NOT NULL CHECK (length(key) BETWEEN 8 AND 128),
    route           text NOT NULL,
    request_hash    text NOT NULL,
    response_status integer,
    response_body   jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

-- ---------------------------------------------------------------------------
-- Защита от изменения неизменяемых журналов
-- ---------------------------------------------------------------------------
CREATE FUNCTION fpv_forbid_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Таблица % доступна только для добавления (операция % запрещена)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_logs_immutable BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION fpv_forbid_modification();
CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION fpv_forbid_modification();
CREATE TRIGGER order_status_history_immutable BEFORE UPDATE OR DELETE ON order_status_history
    FOR EACH ROW EXECUTE FUNCTION fpv_forbid_modification();
CREATE TRIGGER score_transactions_immutable BEFORE UPDATE OR DELETE ON score_transactions
    FOR EACH ROW EXECUTE FUNCTION fpv_forbid_modification();
CREATE TRIGGER video_access_log_immutable BEFORE UPDATE OR DELETE ON video_access_log
    FOR EACH ROW EXECUTE FUNCTION fpv_forbid_modification();

-- Видеоподтверждение нельзя удалить; содержимое (ключ объекта и хеш) нельзя подменить
-- после сохранения. Разрешены только смена статуса проверки/целостности и purge.
CREATE FUNCTION fpv_video_evidence_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Удаление видеоподтверждений запрещено' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ('stored', 'corrupted', 'purged') AND (
        NEW.sha256 IS DISTINCT FROM OLD.sha256 OR
        NEW.object_key IS DISTINCT FROM OLD.object_key OR
        NEW.order_id IS DISTINCT FROM OLD.order_id OR
        NEW.completion_event_at IS DISTINCT FROM OLD.completion_event_at)
    THEN
        RAISE EXCEPTION 'Изменение сохранённого видеоподтверждения запрещено' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'missing' AND NEW.status <> 'missing' THEN
        RAISE EXCEPTION 'Нельзя заменить запись об отсутствии видео' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER video_evidence_guard BEFORE UPDATE OR DELETE ON video_evidence
    FOR EACH ROW EXECUTE FUNCTION fpv_video_evidence_guard();

-- Пользователей нельзя удалять физически: только status = 'deleted'.
CREATE TRIGGER users_no_delete BEFORE DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION fpv_forbid_modification();
