-- 0016_repeat_orders.sql
-- Повторный вылет: заказчик просит повторить выполненный/выполняемый заказ (с комментарием или
-- без), координатор заказа согласует. После согласования создаётся новый заказ — копия маршрута и
-- груза — сразу в очереди операторов, с пометками «Приоритетный» и «Повторный»; в списках техника,
-- операторов и координатора он идёт первым. Число запросов не ограничено.

ALTER TABLE orders
    ADD COLUMN repeat_of uuid REFERENCES orders(id) ON DELETE SET NULL,
    ADD COLUMN priority  boolean NOT NULL DEFAULT false;
CREATE INDEX orders_repeat_of_idx ON orders (repeat_of);

CREATE TABLE repeat_requests (
    id               bigserial PRIMARY KEY,
    order_id         uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    requested_by     uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    comment          text NOT NULL DEFAULT '' CHECK (length(comment) <= 2000),
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by       uuid REFERENCES users(id) ON DELETE RESTRICT,
    decided_at       timestamptz,
    decision_comment text,
    new_order_id     uuid REFERENCES orders(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX repeat_requests_order_idx ON repeat_requests (order_id);
CREATE INDEX repeat_requests_pending_idx ON repeat_requests (status) WHERE status = 'pending';

INSERT INTO permissions (code, description) VALUES
    ('order.repeat.request', 'Запрос повторного вылета по своему заказу'),
    ('order.repeat.decide',  'Согласование повторного вылета (координатор заказа, администратор)')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('customer', 'order.repeat.request'), ('coordinator', 'order.repeat.decide'),
             ('admin', 'order.repeat.decide')) AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;
