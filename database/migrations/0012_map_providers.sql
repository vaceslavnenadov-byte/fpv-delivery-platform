-- 0012_map_providers.sql
-- Онлайн-карты по API-ключу (ESRI, Google, Яндекс, 2ГИС, OpenStreetMap, произвольный XYZ-сервис).
-- Администратор подключает сервис и вводит ключ; браузеры получают плитки через сервер платформы
-- (/api/v1/map-providers/<id>/tiles/z/x/y), поэтому ключ не уходит в браузер и политика
-- безопасности сайта (CSP: img-src 'self') не меняется. Без доступа сервера в интернет онлайн-карты
-- не работают — остаются загруженные карты района.
--
-- Заказчик выбирает карту при оформлении заказа (orders.map_provider_id); у оператора, координатора
-- и администратора по умолчанию показывается она же, каждый может переключиться на другую.

CREATE TABLE map_providers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    preset        text NOT NULL,                       -- esri_imagery, google_roadmap, yandex_map, custom, …
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    url_template  text NOT NULL DEFAULT '' CHECK (url_template = '' OR url_template ~ '^https://'),
    api_key       text NOT NULL DEFAULT '',            -- не возвращается клиентам, в API — только маска
    projection    text NOT NULL DEFAULT '3857' CHECK (projection IN ('3857', '3395')),
    min_zoom      smallint NOT NULL DEFAULT 1 CHECK (min_zoom BETWEEN 0 AND 22),
    max_zoom      smallint NOT NULL DEFAULT 19 CHECK (max_zoom BETWEEN 0 AND 22 AND max_zoom >= min_zoom),
    attribution   text NOT NULL DEFAULT '',
    active        boolean NOT NULL DEFAULT true,
    sort_order    integer NOT NULL DEFAULT 100,
    last_error    text,
    last_error_at timestamptz,
    created_by    uuid REFERENCES users(id) ON DELETE RESTRICT,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE orders ADD COLUMN map_provider_id uuid REFERENCES map_providers(id) ON DELETE SET NULL;

UPDATE permissions SET description = 'Просмотр карт: загруженные карты района и онлайн-карты'
 WHERE code = 'map.read';
UPDATE permissions SET description = 'Загрузка карт района, подключение онлайн-карт и их API-ключей'
 WHERE code = 'map.manage';
