-- 0007_fleets.sql
-- Парки дронов. Техник ведёт свой парк: заводит дроны, выпускает ключи сервисных модулей,
-- проверяет подключение (телеметрия, видео), открывает дроны для заказов и подтверждает
-- первичную готовность дрона к конкретному заказу. База парка — точка А маршрута:
-- А (база) → Б (забор груза у заказчика) → В (получатель).

CREATE TABLE fleets (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    technician_id  uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    base_lat       double precision NOT NULL CHECK (base_lat BETWEEN -90 AND 90),
    base_lon       double precision NOT NULL CHECK (base_lon BETWEEN -180 AND 180),
    base_address   text NOT NULL CHECK (length(base_address) BETWEEN 3 AND 300),
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX fleets_technician_idx ON fleets (technician_id);

ALTER TABLE drones
    ADD COLUMN fleet_id uuid REFERENCES fleets(id) ON DELETE RESTRICT,
    ADD COLUMN published boolean NOT NULL DEFAULT false,
    ADD COLUMN video_check_until timestamptz,
    ADD COLUMN video_checked_at timestamptz;
CREATE INDEX drones_fleet_idx ON drones (fleet_id);

-- Точка А (база парка); у заказов до этой версии — пусто (маршрут А→Б).
ALTER TABLE routes
    ADD COLUMN base_lat double precision CHECK (base_lat BETWEEN -90 AND 90),
    ADD COLUMN base_lon double precision CHECK (base_lon BETWEEN -180 AND 180),
    ADD COLUMN base_address text,
    ADD COLUMN return_m integer CHECK (return_m >= 0);

-- Подтверждение первичной готовности дрона к заказу техником парка.
ALTER TABLE orders
    ADD COLUMN tech_ready_at timestamptz,
    ADD COLUMN tech_ready_by uuid REFERENCES users(id);

INSERT INTO permissions (code, description) VALUES
    ('fleet.manage', 'Ведение своего парка дронов: дроны, ключи, подключение, допуск к заказам'),
    ('order.read_fleet', 'Просмотр заказов на дронах своего парка (техник)');

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, x.perm FROM roles r
JOIN (VALUES ('technician', 'fleet.manage'), ('technician', 'order.read_fleet'),
             ('technician', 'drone.register'), ('technician', 'drone.issue_key')) AS x(role, perm)
  ON r.code = x.role;

-- Дроны теперь заводит техник в своём парке; заказчик выбирает дрон из открытых парков.
DELETE FROM permission_matrix pm USING roles r
 WHERE pm.role_id = r.id AND r.code = 'customer' AND pm.permission IN ('drone.register', 'drone.issue_key');
