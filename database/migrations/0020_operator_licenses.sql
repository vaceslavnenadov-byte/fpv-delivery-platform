-- 0020_operator_licenses.sql
-- Квалификация операторов: допуск к управлению типами БПЛА (справочник «Типы БПЛА») по лицензиям.
-- Оператор загружает лицензию (номер, когда и кем выдана, скан — изображение или PDF); администратор
-- или рекрутёр согласует, возвращает на исправление или отклоняет. Заказы на дроны типа из
-- справочника видят и принимают только операторы с подтверждённой лицензией на этот тип; на дроны,
-- заведённые вручную (без типа), допуск не нужен.

CREATE TABLE operator_licenses (
    id              bigserial PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    drone_model_id  uuid REFERENCES drone_models(id) ON DELETE SET NULL,
    model           jsonb NOT NULL,                     -- параметры типа БПЛА на момент заявки
    license_number  text NOT NULL CHECK (length(btrim(license_number)) BETWEEN 1 AND 100),
    issued_on       date NOT NULL,
    issued_by       text NOT NULL CHECK (length(btrim(issued_by)) BETWEEN 2 AND 300),
    scan_key        text NOT NULL,
    scan_type       text NOT NULL CHECK (scan_type IN ('image/png', 'image/jpeg', 'image/webp', 'application/pdf')),
    scan_name       text NOT NULL DEFAULT '',
    scan_sha256     text NOT NULL,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'fix', 'approved', 'rejected')),
    review_comment  text,
    reviewed_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
-- одна действующая заявка оператора на тип БПЛА (отклонённые остаются в истории)
CREATE UNIQUE INDEX operator_licenses_active_uq ON operator_licenses (user_id, drone_model_id)
    WHERE status IN ('pending', 'fix', 'approved');
CREATE INDEX operator_licenses_status_idx ON operator_licenses (status, created_at);

INSERT INTO permissions (code, description) VALUES
    ('operator.license', 'Свои лицензии на типы БПЛА: загрузка и исправление (оператор)'),
    ('license.review',   'Подтверждение квалификации операторов (лицензий на типы БПЛА)')
ON CONFLICT (code) DO NOTHING;
INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('operator', 'operator.license'), ('admin', 'license.review'), ('recruiter', 'license.review'))
    AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;
