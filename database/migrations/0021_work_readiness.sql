-- 0021_work_readiness.sql
-- Готовность к работе: оператор, координатор и техник отмечают в шапке клиента «Готов к работе» или
-- «Не готов к работе». Неготовый координатор не получает новые заказы (их распределяют между готовыми,
-- его текущие заказы остаются за ним); неготовый оператор не видит очередь и не принимает заказы;
-- дроны неготового техника не принимают новые заказы (парк на паузе). Нет записи — готов.

CREATE TABLE work_readiness (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('operator', 'coordinator', 'technician')),
    ready       boolean NOT NULL DEFAULT true,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role)
);

INSERT INTO permissions (code, description) VALUES
    ('work.readiness', 'Отметка «Готов / Не готов к работе» (оператор, координатор, техник)')
ON CONFLICT (code) DO NOTHING;
INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'work.readiness' FROM roles r WHERE r.code IN ('operator', 'coordinator', 'technician')
ON CONFLICT DO NOTHING;
