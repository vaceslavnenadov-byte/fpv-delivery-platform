-- 0018_catalogs_cargo_archive.sql
-- 1) Справочники администратора:
--    * типы грузов (cargo_types): название, тип (категория), максимальная масса — из них заказчик
--      выбирает «Тип груза»;
--    * типы БПЛА (drone_models): модель, производитель, тип связи, конструкция, тип двигателя,
--      дальность, грузоподъёмность — техник выбирает конфигурацию при заведении дрона.
-- 2) Несколько грузов в одном заказе (order_cargo_items); orders.cargo_* — сводка (масса — сумма,
--    габариты — наибольшие, тип и описание — первого груза).
-- 3) Архив БПЛА: перенос дрона из парка в архив и обратно с причиной.
-- 4) Профиль: никнейм и местоположение.

CREATE TABLE cargo_types (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]{2,40}$'),
    name         text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    category     text NOT NULL DEFAULT '' CHECK (length(category) <= 120),
    max_weight_g integer NOT NULL CHECK (max_weight_g BETWEEN 1 AND 100000),
    deleted_at   timestamptz,
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX cargo_types_name_uq ON cargo_types (lower(name)) WHERE deleted_at IS NULL;

INSERT INTO cargo_types (code, name, category, max_weight_g) VALUES
    ('documents', 'Документы', 'Документы', 2000),
    ('parcel', 'Посылка', 'Посылки', 2000),
    ('food', 'Продукты питания', 'Продукты', 2000),
    ('medical_supplies', 'Медицинские изделия', 'Медицина', 2000),
    ('spare_parts', 'Запчасти', 'Техника', 2000)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE drone_models (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model        text NOT NULL CHECK (length(btrim(model)) BETWEEN 1 AND 100),
    manufacturer text NOT NULL CHECK (length(btrim(manufacturer)) BETWEEN 2 AND 100),
    link_type    text NOT NULL DEFAULT '' CHECK (length(link_type) <= 100),
    construction text NOT NULL DEFAULT '' CHECK (length(construction) <= 100),
    engine_type  text NOT NULL DEFAULT '' CHECK (length(engine_type) <= 100),
    max_range_m  integer NOT NULL CHECK (max_range_m BETWEEN 500 AND 100000),
    max_payload_g integer NOT NULL CHECK (max_payload_g BETWEEN 50 AND 25000),
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX drone_models_uq ON drone_models (lower(manufacturer), lower(model));

ALTER TABLE drones
    ADD COLUMN drone_model_id  uuid REFERENCES drone_models(id) ON DELETE SET NULL,
    ADD COLUMN link_type       text,
    ADD COLUMN construction    text,
    ADD COLUMN engine_type     text,
    ADD COLUMN archived_at     timestamptz,
    ADD COLUMN archived_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN archive_reason  text CHECK (archive_reason IN ('broken', 'lost', 'repair', 'returned', 'worn_out',
                                                             'dismantled', 'obsolete', 'peripherals', 'other')),
    ADD COLUMN archive_comment text;

CREATE TABLE order_cargo_items (
    id          bigserial PRIMARY KEY,
    order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    position    smallint NOT NULL,
    cargo_type  text NOT NULL,
    description text NOT NULL CHECK (length(description) BETWEEN 3 AND 1000),
    weight_g    integer NOT NULL CHECK (weight_g BETWEEN 1 AND 100000),
    length_mm   integer NOT NULL CHECK (length_mm BETWEEN 1 AND 5000),
    width_mm    integer NOT NULL CHECK (width_mm BETWEEN 1 AND 5000),
    height_mm   integer NOT NULL CHECK (height_mm BETWEEN 1 AND 5000),
    UNIQUE (order_id, position)
);
-- у оформленных заказов — один груз из сводных полей
INSERT INTO order_cargo_items (order_id, position, cargo_type, description, weight_g, length_mm, width_mm, height_mm)
SELECT id, 1, cargo_type, cargo_description, cargo_weight_g, cargo_length_mm, cargo_width_mm, cargo_height_mm
  FROM orders WHERE length(cargo_description) >= 3;

ALTER TABLE users
    ADD COLUMN nickname text CHECK (length(nickname) <= 60),
    ADD COLUMN location text CHECK (length(location) <= 200);

INSERT INTO permissions (code, description) VALUES
    ('catalog.manage', 'Справочники: типы грузов и типы БПЛА'),
    ('drone.archive',  'Архив БПЛА своего парка: перенос в архив и обратно')
ON CONFLICT (code) DO NOTHING;
INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('admin', 'catalog.manage'), ('technician', 'drone.archive')) AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;
