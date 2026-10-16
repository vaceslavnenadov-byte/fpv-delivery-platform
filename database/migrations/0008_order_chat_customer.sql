-- 0008_order_chat_customer.sql
-- «Связь по заказу»: в переписке по заказу участвуют заказчик, оператор, координатор (и администратор).
-- Служебные замечания и инциденты координатора заказчику по-прежнему не показываются.

UPDATE permissions SET description = 'Связь по заказу: переписка участников заказа (заказчик, оператор, координатор)'
 WHERE code = 'order.chat';

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'order.chat' FROM roles r WHERE r.code = 'customer'
ON CONFLICT DO NOTHING;
