-- 0009_dispute_coordinator_review.sql
-- Претензии (споры по доставке) сначала рассматривает координатор заказа:
--   «Отклонить претензию» — спор закрывается (статус dismissed), заказ возвращается в прежний ход;
--   «Удовлетворить претензию» — решение координатора фиксируется, администратору приходит
--   уведомление, и окончательное решение (с корректировкой баллов) принимает администратор.

ALTER TABLE complaints
    ADD COLUMN coordinator_verdict text CHECK (coordinator_verdict IN ('upheld', 'dismissed')),
    ADD COLUMN coordinator_comment text,
    ADD COLUMN coordinator_id      uuid REFERENCES users(id) ON DELETE RESTRICT,
    ADD COLUMN coordinator_at      timestamptz,
    ADD CONSTRAINT complaints_coordinator_review_ck CHECK (
        (coordinator_verdict IS NULL AND coordinator_id IS NULL AND coordinator_at IS NULL)
        OR (coordinator_verdict IS NOT NULL AND coordinator_id IS NOT NULL AND coordinator_at IS NOT NULL
            AND length(coordinator_comment) >= 5));

INSERT INTO permissions (code, description) VALUES
    ('complaint.review', 'Предварительное рассмотрение претензий по сопровождаемым заказам')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'complaint.review' FROM roles r WHERE r.code = 'coordinator'
ON CONFLICT DO NOTHING;
