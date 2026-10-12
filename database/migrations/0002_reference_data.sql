-- 0002_reference_data.sql
-- Роли приложения, каталог разрешений, таблица прав и правила начисления баллов по умолчанию.
-- Таблица прав (permission_matrix) — единственный источник истины для backend:
-- каждый защищённый endpoint проверяет конкретное разрешение, а не роль.

INSERT INTO roles (code, name, description) VALUES
    ('customer',    'Заказчик',              'Создаёт заказы, регистрирует собственные дроны'),
    ('operator',    'Оператор дрона',        'Принимает и выполняет заказы (после подтверждения администратором)'),
    ('coordinator', 'Наводчик / координатор','Координирует и контролирует выполнение назначенных заказов'),
    ('technician',  'Техник',                'Техническое обслуживание и допуск дронов'),
    ('admin',       'Администратор',         'Управление пользователями, проверка результатов, аудит');

INSERT INTO permissions (code, description) VALUES
    ('profile.self',              'Просмотр и заполнение собственного профиля'),
    ('notification.read',         'Чтение собственных уведомлений'),
    ('drone.register',            'Регистрация собственного дрона'),
    ('drone.read_own',            'Просмотр собственных дронов'),
    ('drone.issue_key',           'Выпуск ключа подключения для собственного дрона'),
    ('drone.read_all',            'Просмотр всех зарегистрированных дронов'),
    ('drone.approve',             'Подтверждение регистрации и блокировка дронов'),
    ('drone.health.write',        'Установка технического статуса и запись результатов осмотра'),
    ('maintenance.read',          'Просмотр журнала обслуживания'),
    ('maintenance.write',         'Ведение журнала обслуживания'),
    ('order.create',              'Создание и отправка собственных заказов'),
    ('order.read_own',            'Просмотр собственных заказов'),
    ('order.cancel_own',          'Отмена собственного заказа до начала выполнения'),
    ('order.confirm_receipt',     'Подтверждение получения груза'),
    ('complaint.create',          'Запрос рассмотрения спорной ситуации'),
    ('operator.apply',            'Подача заявки на допуск оператора'),
    ('order.queue.read',          'Просмотр очереди доступных заказов'),
    ('order.accept',              'Принятие и отклонение заказа'),
    ('order.execute',             'Проверка готовности, начало, завершение, прерывание задания'),
    ('order.report',              'Формирование отчёта о выполнении'),
    ('order.read_assigned',       'Просмотр назначенных заказов'),
    ('telemetry.read_assigned',   'Просмотр телеметрии назначенных заказов'),
    ('order.note.write',          'Замечания, инциденты, сообщения оператору'),
    ('score.read_own',            'Просмотр собственных баллов и рейтинга'),
    ('score.appeal',              'Обжалование оценки'),
    ('order.read_all',            'Просмотр всех заказов'),
    ('order.assign',              'Назначение координатора заказу'),
    ('order.review',              'Принятие и отклонение отчётов о выполнении, подтверждение баллов'),
    ('telemetry.read_all',        'Просмотр телеметрии любых заказов'),
    ('video.read',                'Просмотр видеоподтверждений'),
    ('complaint.resolve',         'Рассмотрение жалоб и споров, корректировка баллов'),
    ('scoring.manage',            'Управление правилами начисления баллов'),
    ('user.manage',               'Управление пользователями, ролями и сопоставлением доменных групп'),
    ('operator.approve',          'Подтверждение регистрации операторов'),
    ('device.manage',             'Управление устройствами и политиками доступа'),
    ('audit.read',                'Просмотр журнала аудита'),
    ('report.read',               'Статистические отчёты'),
    ('system.monitor',            'Мониторинг серверов, сервисов и подключённых устройств');

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.permission
FROM (VALUES
    ('customer', 'profile.self'), ('customer', 'notification.read'),
    ('customer', 'drone.register'), ('customer', 'drone.read_own'), ('customer', 'drone.issue_key'),
    ('customer', 'order.create'), ('customer', 'order.read_own'), ('customer', 'order.cancel_own'),
    ('customer', 'order.confirm_receipt'), ('customer', 'complaint.create'), ('customer', 'operator.apply'),

    ('operator', 'profile.self'), ('operator', 'notification.read'), ('operator', 'operator.apply'),
    ('operator', 'order.queue.read'), ('operator', 'order.accept'), ('operator', 'order.execute'),
    ('operator', 'order.report'), ('operator', 'order.read_assigned'), ('operator', 'telemetry.read_assigned'),
    ('operator', 'score.read_own'), ('operator', 'score.appeal'),

    ('coordinator', 'profile.self'), ('coordinator', 'notification.read'),
    ('coordinator', 'order.read_assigned'), ('coordinator', 'telemetry.read_assigned'),
    ('coordinator', 'order.note.write'),

    ('technician', 'profile.self'), ('technician', 'notification.read'),
    ('technician', 'drone.read_all'), ('technician', 'drone.health.write'),
    ('technician', 'maintenance.read'), ('technician', 'maintenance.write'),

    ('admin', 'profile.self'), ('admin', 'notification.read'),
    ('admin', 'drone.read_all'), ('admin', 'drone.approve'), ('admin', 'maintenance.read'),
    ('admin', 'order.read_all'), ('admin', 'order.assign'), ('admin', 'order.review'),
    ('admin', 'telemetry.read_all'), ('admin', 'video.read'), ('admin', 'complaint.resolve'),
    ('admin', 'scoring.manage'), ('admin', 'user.manage'), ('admin', 'operator.approve'),
    ('admin', 'device.manage'), ('admin', 'audit.read'), ('admin', 'report.read'),
    ('admin', 'system.monitor')
) AS p(role_code, permission)
JOIN roles r ON r.code = p.role_code;

-- Сопоставление доменных групп с ролями по умолчанию. DN приведены как пример:
-- фактические значения задаются администратором (или через переменные Ansible
-- fpv_domain_group_mapping) после согласования со службой каталогов.
INSERT INTO domain_groups (dn, name, role_id, description)
SELECT g.dn, g.name, r.id, g.description
FROM (VALUES
    ('CN=FPV-Customers,OU=FPV,OU=Groups,DC=fpv,DC=local',    'FPV-Customers',    'customer',    'Заказчики'),
    ('CN=FPV-Operators,OU=FPV,OU=Groups,DC=fpv,DC=local',    'FPV-Operators',    'operator',    'Операторы дронов'),
    ('CN=FPV-Coordinators,OU=FPV,OU=Groups,DC=fpv,DC=local', 'FPV-Coordinators', 'coordinator', 'Наводчики / координаторы'),
    ('CN=FPV-Technicians,OU=FPV,OU=Groups,DC=fpv,DC=local',  'FPV-Technicians',  'technician',  'Техники'),
    ('CN=FPV-Admins,OU=FPV,OU=Groups,DC=fpv,DC=local',       'FPV-Admins',       'admin',       'Администраторы платформы')
) AS g(dn, name, role_code, description)
JOIN roles r ON r.code = g.role_code;

INSERT INTO scoring_rules (rules, is_active, comment) VALUES (
    '{
        "time_window": {"max_points": 30, "grace_minutes": 30},
        "result": {
            "receipt_confirmed": 20,
            "report_submitted": 10,
            "video_verified": 20,
            "admin_approved": 20,
            "upheld_complaint_penalty": 20
        }
    }'::jsonb,
    true,
    'Правила по умолчанию: окно 30 + результат 70 = 100'
);
