-- 0004_flight_monitor_chat.sql
-- Монитор полёта: переписка оператора и координатора по заказу (с кадром с камеры дрона).
-- Сообщения хранятся в order_notes (вид 'chat'); кадр — в объектном хранилище с контрольной суммой.

ALTER TABLE order_notes DROP CONSTRAINT order_notes_kind_check;
ALTER TABLE order_notes ADD CONSTRAINT order_notes_kind_check
    CHECK (kind IN ('remark', 'message_to_operator', 'incident', 'deviation', 'chat'));

ALTER TABLE order_notes
    ADD COLUMN attachment_key    text,
    ADD COLUMN attachment_type   text CHECK (attachment_type IN ('image/jpeg')),
    ADD COLUMN attachment_sha256 text CHECK (attachment_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT order_notes_attachment_complete
        CHECK ((attachment_key IS NULL) = (attachment_type IS NULL)
               AND (attachment_key IS NULL) = (attachment_sha256 IS NULL));

INSERT INTO permissions (code, description) VALUES
    ('order.chat', 'Переписка оператора и координатора по назначенному заказу');

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.permission
FROM (VALUES ('operator', 'order.chat'), ('coordinator', 'order.chat'), ('admin', 'order.chat'))
     AS p(role_code, permission)
JOIN roles r ON r.code = p.role_code;
