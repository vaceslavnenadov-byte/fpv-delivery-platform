-- 0014_service_zones.sql
-- Несколько зон обслуживания вместо одной. Администратор создаёт сколько угодно зон
-- (прямоугольников) и назначает в каждую любое число техников; техник может работать в нескольких
-- зонах. Зона без назначенных техников открыта всем техникам.
--
-- База парка должна лежать в зоне, куда назначен техник парка; парк привязывается к этой зоне
-- (fleets.zone_id), и все точки маршрутов заказов на дроны парка должны быть в ней же.
--
-- Переход с одной зоны (0013): зона, заданная администратором, становится «Основной зоной».
-- Если её не задавали, «Основная зона» создаётся при первом обращении из FPV_SERVICE_AREA.

CREATE TABLE service_zones (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 120),
    south      double precision NOT NULL CHECK (south BETWEEN -85 AND 85),
    west       double precision NOT NULL CHECK (west BETWEEN -180 AND 180),
    north      double precision NOT NULL CHECK (north BETWEEN -85 AND 85),
    east       double precision NOT NULL CHECK (east BETWEEN -180 AND 180),
    active     boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (south < north AND west < east)
);

CREATE TABLE service_zone_technicians (
    zone_id       uuid NOT NULL REFERENCES service_zones(id) ON DELETE CASCADE,
    technician_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    assigned_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (zone_id, technician_id)
);
CREATE INDEX service_zone_technicians_tech_idx ON service_zone_technicians (technician_id);

ALTER TABLE fleets ADD COLUMN zone_id uuid REFERENCES service_zones(id) ON DELETE SET NULL;

INSERT INTO service_zones (name, south, west, north, east, updated_by)
SELECT 'Основная зона', (value->>'south')::float8, (value->>'west')::float8,
       (value->>'north')::float8, (value->>'east')::float8, updated_by
  FROM platform_settings WHERE key = 'service_area';

INSERT INTO platform_settings (key, value)
SELECT 'zones_seeded', 'true'::jsonb WHERE EXISTS (SELECT 1 FROM service_zones)
ON CONFLICT (key) DO NOTHING;

UPDATE fleets f SET zone_id = z.id FROM service_zones z
 WHERE f.base_lat BETWEEN z.south AND z.north AND f.base_lon BETWEEN z.west AND z.east;

DELETE FROM platform_settings WHERE key = 'service_area';

UPDATE permissions SET description = 'Зоны обслуживания и назначение в них техников'
 WHERE code = 'platform.settings';
