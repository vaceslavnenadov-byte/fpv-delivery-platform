-- 0019_consignees_coordinator_approval.sql
-- 1) Справочник «Типы грузополучателей» (ведёт администратор): название, тип, рекомендованный груз,
--    местоположение (комментарий), приоритет, комментарий. Заказчик выбирает грузополучателя из него.
-- 2) Приоритет заказа (по грузополучателю; повторный вылет — не ниже «высокого»).
-- 3) Новый заказ сначала согласует координатор (статус awaiting_coordinator), согласовавший
--    закрепляется за заказом. Проверку и оценку выполнения проводит координатор, а не администратор.
-- 4) Маршрут заказчика: груз забирается на базе (А), дальше точки Б, В, …; последняя — получатель.
-- 5) Время доставки и телефон получателя больше не обязательны; комментарий заказчика с фото.
-- 6) Проверка профилей: пока профиль не подтверждён впервые, обязанности недоступны.

CREATE TABLE consignees (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    kind                 text NOT NULL DEFAULT '' CHECK (length(kind) <= 120),
    recommended_cargo    text,                              -- код из cargo_types
    location             text NOT NULL DEFAULT '' CHECK (length(location) <= 500),
    priority             text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'highest')),
    comment              text NOT NULL DEFAULT '' CHECK (length(comment) <= 2000),
    deleted_at           timestamptz,
    created_by           uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX consignees_name_uq ON consignees (lower(name)) WHERE deleted_at IS NULL;

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN (
    'draft', 'pending_confirmation', 'awaiting_coordinator', 'awaiting_operator', 'accepted',
    'awaiting_readiness', 'ready', 'in_progress', 'awaiting_delivery_confirmation', 'admin_review',
    'completed', 'rejected', 'cancelled', 'aborted_technical', 'disputed'));
ALTER TABLE orders ALTER COLUMN recipient_phone DROP NOT NULL;
ALTER TABLE orders
    ADD COLUMN consignee_id            uuid REFERENCES consignees(id) ON DELETE SET NULL,
    ADD COLUMN consignee               jsonb,             -- сведения о грузополучателе на момент заказа
    ADD COLUMN priority_level          text NOT NULL DEFAULT 'normal'
                                       CHECK (priority_level IN ('low', 'normal', 'high', 'highest')),
    ADD COLUMN coordinator_approved_at timestamptz,
    ADD COLUMN customer_comment        text CHECK (length(customer_comment) <= 2000),
    ADD COLUMN comment_photo_key       text,
    ADD COLUMN comment_photo_type      text,
    ADD COLUMN asap                    boolean NOT NULL DEFAULT false;   -- без заданного времени доставки
UPDATE orders SET priority_level = 'high' WHERE priority;
-- уже размещённые заказы считаются согласованными (порядок вылета одного дрона — по этому времени)
UPDATE orders SET coordinator_approved_at = coalesce(submitted_at, created_at)
 WHERE status NOT IN ('draft', 'pending_confirmation');
CREATE INDEX orders_drone_queue_idx ON orders (drone_id, coordinator_approved_at) WHERE status = 'awaiting_operator';

ALTER TABLE routes ADD COLUMN pickup_at_base boolean NOT NULL DEFAULT false;

ALTER TABLE users ADD COLUMN profile_verified_at timestamptz;
-- действующие учётные записи сохраняют доступ к обязанностям
UPDATE users SET profile_verified_at = coalesce(profile_reviewed_at, created_at, now());

INSERT INTO permissions (code, description) VALUES
    ('order.approve',       'Согласование новых заказов (координатор)'),
    ('order.map',           'Карта заказов: все дроны в работе, маршруты и точки доставки'),
    ('video.read_assigned', 'Просмотр видеоподтверждений своих заказов')
ON CONFLICT (code) DO NOTHING;
UPDATE permissions SET description = 'Проверка и оценка выполнения заказа (координатор заказа)' WHERE code = 'order.review';
DELETE FROM permission_matrix pm USING roles r
 WHERE pm.role_id = r.id AND r.code = 'admin' AND pm.permission = 'order.review';
INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('coordinator', 'order.review'), ('coordinator', 'order.approve'), ('coordinator', 'video.read_assigned'),
             ('coordinator', 'order.map'), ('admin', 'order.map')) AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;

-- Фото к комментарию заказчика попадает в переписку по заказу: PNG и WebP вдобавок к JPEG.
ALTER TABLE order_notes DROP CONSTRAINT IF EXISTS order_notes_attachment_type_check;
ALTER TABLE order_notes ADD CONSTRAINT order_notes_attachment_type_check
    CHECK (attachment_type IN ('image/jpeg', 'image/png', 'image/webp'));
