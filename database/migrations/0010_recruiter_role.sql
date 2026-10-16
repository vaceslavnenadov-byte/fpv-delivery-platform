-- 0010_recruiter_role.sql
-- Роль «Рекрутер»: добавляет пользователей из доменного каталога в систему, выдаёт и снимает
-- роли заказчика, оператора, координатора и техника, подтверждает профили пользователей и
-- рассматривает заявки на допуск операторов. Администратор сохраняет все эти права.
-- Роли «Администратор» и «Рекрутер» рекрутер выдавать не может — только администратор.

INSERT INTO roles (code, name, description) VALUES
    ('recruiter', 'Рекрутер', 'Добавление пользователей, выдача ролей, подтверждение профилей и допуска операторов')
ON CONFLICT (code) DO NOTHING;

INSERT INTO domain_groups (dn, name, role_id, description)
SELECT 'CN=FPV-Recruiters,OU=FPV,OU=Groups,DC=fpv,DC=local', 'FPV-Recruiters', r.id, 'Рекрутеры'
FROM roles r WHERE r.code = 'recruiter'
ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, description) VALUES
    ('user.recruit', 'Добавление пользователей из каталога, выдача ролей (кроме администратора и рекрутера), подтверждение профилей')
ON CONFLICT (code) DO NOTHING;

INSERT INTO permission_matrix (role_id, permission)
SELECT r.id, p.perm FROM roles r
JOIN (VALUES ('recruiter', 'profile.self'), ('recruiter', 'notification.read'),
             ('recruiter', 'user.recruit'), ('recruiter', 'operator.approve'),
             ('admin', 'user.recruit')) AS p(role_code, perm) ON p.role_code = r.code
ON CONFLICT DO NOTHING;

-- Проверка профиля: pending — ждёт проверки, verified — подтверждён, rejected — возвращён на доработку.
ALTER TABLE users
    ADD COLUMN profile_status      text NOT NULL DEFAULT 'pending'
                                   CHECK (profile_status IN ('pending', 'verified', 'rejected')),
    ADD COLUMN profile_reviewed_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    ADD COLUMN profile_reviewed_at timestamptz,
    ADD COLUMN profile_review_notes text,
    ADD COLUMN registered_by       uuid REFERENCES users(id) ON DELETE RESTRICT;

-- Уже заполненные до обновления профили считаются подтверждёнными (их проверял администратор).
UPDATE users SET profile_status = 'verified' WHERE profile_completed;
