-- 0017_support.sql
-- Техподдержка: обращения пользователей к администратору в виде переписки (чата) с фото и видео.
-- Причины: по заказу, по аккаунту, по группе, по работе сервиса, предложения, другое.
-- Статусы: new (новое), in_progress (в работе), rejected (отклонено), done (выполнено) — видны всем
-- участникам переписки. Администратор может добавить в обсуждение других пользователей.

CREATE TABLE support_tickets (
    id                bigserial PRIMARY KEY,
    author_id         uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reason            text NOT NULL CHECK (reason IN ('order', 'account', 'group', 'service', 'suggestion', 'other')),
    subject           text NOT NULL CHECK (length(btrim(subject)) BETWEEN 3 AND 200),
    order_id          uuid REFERENCES orders(id) ON DELETE SET NULL,
    status            text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'in_progress', 'rejected', 'done')),
    status_changed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    status_changed_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_message_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX support_tickets_status_idx ON support_tickets (status, created_at DESC);
CREATE INDEX support_tickets_author_idx ON support_tickets (author_id);

CREATE TABLE support_participants (
    ticket_id    bigint NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    added_at     timestamptz NOT NULL DEFAULT now(),
    last_read_at timestamptz,
    PRIMARY KEY (ticket_id, user_id)
);
CREATE INDEX support_participants_user_idx ON support_participants (user_id);

CREATE TABLE support_messages (
    id         bigserial PRIMARY KEY,
    ticket_id  bigint NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    author_id  uuid REFERENCES users(id) ON DELETE SET NULL,      -- NULL — системное сообщение
    body       text NOT NULL DEFAULT '' CHECK (length(body) <= 5000),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX support_messages_ticket_idx ON support_messages (ticket_id, id);

CREATE TABLE support_attachments (
    id           bigserial PRIMARY KEY,
    message_id   bigint NOT NULL REFERENCES support_messages(id) ON DELETE CASCADE,
    object_key   text NOT NULL UNIQUE,
    content_type text NOT NULL,
    size_bytes   bigint NOT NULL,
    sha256       text NOT NULL,
    filename     text NOT NULL DEFAULT ''
);

INSERT INTO permissions (code, description) VALUES
    ('support.use',    'Обращения в техподдержку: создание, переписка, вложения'),
    ('support.manage', 'Обращения в ТП: все обращения, статусы, добавление участников')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'support.use' FROM roles r ON CONFLICT DO NOTHING;
INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, 'support.manage' FROM roles r WHERE r.code = 'admin' ON CONFLICT DO NOTHING;
