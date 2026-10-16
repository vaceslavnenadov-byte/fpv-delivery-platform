-- 0006_video_sources.sql
-- Источники видео дрона: список, который сообщает сервисный модуль (USB-камеры, RTSP-потоки,
-- тестовые ролики), активный источник и выбор оператора. Выбор — настройка видео, не управление полётом.

ALTER TABLE drones
    ADD COLUMN video_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN video_source_active text,
    ADD COLUMN video_source_selected text,
    ADD COLUMN video_source_selected_by uuid REFERENCES users(id),
    ADD COLUMN video_source_selected_at timestamptz;
