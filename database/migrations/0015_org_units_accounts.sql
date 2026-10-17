-- 0015_org_units_accounts.sql
-- 1) Внутренние группы пользователей: дерево любой глубины (компания → отдел → кабинет → …),
--    чисто информационное — на права не влияет. У каждой группы можно создать подгруппу.
-- 2) Анкета пользователя: ФИО, должность в организации, примечания, группа.
-- 3) Учётные записи, созданные в приложении (логин и пароль задаёт рекрутер или администратор),
--    наряду с доменными; смена пароля фиксируется (кто и когда).
-- 4) Статусы учётной записи: active, suspended (вход есть, обязанности недоступны),
--    banned (вход запрещён: «Ваш аккаунт забанен. Обратитесь в техподдержку»), disabled, deleted.

CREATE TABLE org_units (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id  uuid REFERENCES org_units(id) ON DELETE RESTRICT,
    name       text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 160),
    created_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_id IS NULL OR parent_id <> id)
);
-- имя уникально среди «соседей» (у корня — среди корневых групп)
CREATE UNIQUE INDEX org_units_sibling_name_uq
    ON org_units (COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check;
ALTER TABLE users
    ADD CONSTRAINT users_status_check CHECK (status IN ('active', 'suspended', 'banned', 'disabled', 'deleted')),
    ADD COLUMN auth_source         text NOT NULL DEFAULT 'domain' CHECK (auth_source IN ('domain', 'local')),
    ADD COLUMN password_hash       text,
    ADD COLUMN password_changed_at timestamptz,
    ADD COLUMN password_changed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN status_reason       text,
    ADD COLUMN status_changed_at   timestamptz,
    ADD COLUMN status_changed_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN last_name           text,
    ADD COLUMN first_name          text,
    ADD COLUMN middle_name         text,
    ADD COLUMN position_title      text,
    ADD COLUMN profile_notes       text,
    ADD COLUMN org_unit_id         uuid REFERENCES org_units(id) ON DELETE SET NULL,
    ADD CONSTRAINT users_local_password_ck CHECK (auth_source = 'domain' OR password_hash IS NOT NULL);
CREATE INDEX users_org_unit_idx ON users (org_unit_id);

INSERT INTO permissions (code, description) VALUES
    ('org.manage',        'Внутренние группы и подгруппы пользователей: создание, переименование, удаление'),
    ('user.status',       'Бан, разбан и приостановка учётных записей'),
    ('user.credentials',  'Смена логинов и паролей учётных записей приложения'),
    ('user.profile.edit', 'Заполнение и редактирование анкет пользователей')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('admin', 'org.manage'), ('admin', 'user.status'), ('admin', 'user.credentials'),
             ('admin', 'user.profile.edit')) AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;
