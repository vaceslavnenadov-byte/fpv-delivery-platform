-- Демонстрационные устройства и политики доступа (стенд разработки/тестирования).
-- Применение: psql "$DATABASE_URL" -f database/seeds/dev_devices.sql
-- Повторный запуск безопасен (ON CONFLICT DO NOTHING).

INSERT INTO devices (code, kind, hostname, description, domain_joined) VALUES
    ('WS-OPS-01',  'workstation',    'ws-ops-01.fpv.local',  'Рабочее место оператора',    true),
    ('WS-COORD-01','workstation',    'ws-coord-01.fpv.local','Рабочее место координатора', true),
    ('WS-TECH-01', 'workstation',    'ws-tech-01.fpv.local', 'Рабочее место техника',      true),
    ('WS-ADM-01',  'workstation',    'ws-adm-01.fpv.local',  'Рабочее место администратора', true),
    ('GS-01',      'ground_station', 'gs-01.fpv.local',      'Наземная станция (сервисный модуль дрона)', true)
ON CONFLICT (code) DO NOTHING;

INSERT INTO device_access_policies (device_id, role_id, access_level)
SELECT d.id, r.id, p.level
FROM (VALUES
    ('WS-OPS-01', 'operator', 'use'),
    ('WS-OPS-01', 'admin', 'admin'),
    ('WS-COORD-01', 'coordinator', 'use'),
    ('WS-COORD-01', 'admin', 'admin'),
    ('WS-TECH-01', 'technician', 'use'),
    ('WS-TECH-01', 'admin', 'admin'),
    ('WS-ADM-01', 'admin', 'admin')
) AS p(code, role, level)
JOIN devices d ON d.code = p.code
JOIN roles r ON r.code = p.role
ON CONFLICT DO NOTHING;
