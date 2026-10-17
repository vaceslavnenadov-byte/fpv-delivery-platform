-- 0013_platform_settings.sql
-- Настройки платформы, которые администратор меняет в интерфейсе (без правки файлов на сервере).
-- Первая такая настройка — зона обслуживания (service_area): прямоугольник, внутри которого должны
-- лежать базы парков и все точки маршрутов. Пока администратор её не задал, действует значение
-- FPV_SERVICE_AREA из /etc/fpv/fpv.env (по умолчанию — Москва).

CREATE TABLE platform_settings (
    key        text PRIMARY KEY CHECK (key ~ '^[a-z_]{2,64}$'),
    value      jsonb NOT NULL,
    updated_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO permissions (code, description) VALUES
    ('platform.settings', 'Настройки платформы: зона обслуживания')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'platform.settings' FROM roles r WHERE r.code = 'admin'
ON CONFLICT DO NOTHING;
