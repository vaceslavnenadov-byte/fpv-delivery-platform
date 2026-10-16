-- 0005_customer_stream.sql
-- Трансляция с камеры заказчика: публикация видео из браузера по своему заказу.
-- Видео заказчика не хранится в БД и не является доказательной базой (кольцевой буфер на диске).

INSERT INTO permissions (code, description) VALUES
    ('video.publish', 'Трансляция с собственной камеры по своему заказу (заказчик)');

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'video.publish' FROM roles r WHERE r.code = 'customer';
