-- 0022_auto_dispatch.sql
-- Распределение заказов с учётом готовности к работе (вместо паузы парка и общей очереди из 0021):
-- * оператору заказ назначает система — готовому, с подтверждённой квалификацией на тип БПЛА и с
--   наименьшей нагрузкой; оператор принимает или отклоняет предложение (offered_operator_id);
-- * заказы на дронах парка неготового техника ведёт готовый техник той же зоны (duty_technician_id).

ALTER TABLE orders ADD COLUMN offered_operator_id uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE orders ADD COLUMN offered_at timestamptz;
ALTER TABLE orders ADD COLUMN duty_technician_id uuid REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX orders_offered_operator_idx ON orders (offered_operator_id) WHERE offered_operator_id IS NOT NULL;
CREATE INDEX orders_duty_technician_idx ON orders (duty_technician_id) WHERE duty_technician_id IS NOT NULL;
