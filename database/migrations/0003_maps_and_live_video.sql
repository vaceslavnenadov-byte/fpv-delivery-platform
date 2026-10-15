-- 0003_maps_and_live_video.sql
-- Карты-подложки (загружаются администратором, работают без доступа в интернет)
-- и прямая видеотрансляция выполняемого заказа.

CREATE TABLE map_layers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description   text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    object_key    text NOT NULL UNIQUE,
    content_type  text NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg', 'image/webp')),
    size_bytes    bigint NOT NULL CHECK (size_bytes > 0),
    sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    width_px      integer CHECK (width_px > 0),
    height_px     integer CHECK (height_px > 0),
    -- Географические границы изображения (WGS 84, градусы): север/юг — широта, запад/восток — долгота.
    north         double precision NOT NULL CHECK (north BETWEEN -85 AND 85),
    south         double precision NOT NULL CHECK (south BETWEEN -85 AND 85),
    west          double precision NOT NULL CHECK (west BETWEEN -180 AND 180),
    east          double precision NOT NULL CHECK (east BETWEEN -180 AND 180),
    active        boolean NOT NULL DEFAULT true,
    uploaded_by   uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (north > south),
    CHECK (east > west)
);

CREATE INDEX map_layers_active_idx ON map_layers (active);

INSERT INTO permissions (code, description) VALUES
    ('map.read',   'Просмотр карт-подложек'),
    ('map.manage', 'Загрузка и управление картами-подложками'),
    ('video.live', 'Просмотр прямой видеотрансляции выполняемого заказа (в пределах доступа к заказу)');

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.permission
FROM (VALUES
    ('customer', 'map.read'), ('operator', 'map.read'), ('coordinator', 'map.read'),
    ('technician', 'map.read'), ('admin', 'map.read'), ('admin', 'map.manage'),
    ('customer', 'video.live'), ('operator', 'video.live'), ('coordinator', 'video.live'),
    ('admin', 'video.live')
) AS p(role_code, permission)
JOIN roles r ON r.code = p.role_code;
