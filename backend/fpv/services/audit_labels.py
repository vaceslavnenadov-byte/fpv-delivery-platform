"""Подписи журнала аудита по-русски: действия, типы объектов, итоги, разделы фильтра.

Код действия в журнале не меняется (по нему строится цепочка хешей и фильтр); подпись — только для показа.
Неизвестный код показывается как есть.
"""
from __future__ import annotations

from typing import Dict

from .people import HISTORY_LABELS

ACTION_LABELS: Dict[str, str] = {
    **HISTORY_LABELS,
    "audit.verified": "Проверена цепочка хешей журнала",
    "auth.login": "Вход в систему",
    "auth.logout": "Выход из системы",
    "auth.revalidate": "Повторная проверка учётной записи в домене",
    "auth.access_revoked": "Доступ отозван",
    "catalog.cargo_created": "Добавлен вид груза",
    "catalog.cargo_updated": "Изменён вид груза",
    "catalog.cargo_deleted": "Удалён вид груза",
    "catalog.consignee_created": "Добавлен грузополучатель",
    "catalog.consignee_updated": "Изменён грузополучатель",
    "catalog.consignee_deleted": "Удалён грузополучатель",
    "catalog.drone_model_created": "Добавлен тип БПЛА",
    "catalog.drone_model_updated": "Изменён тип БПЛА",
    "catalog.drone_model_deleted": "Удалён тип БПЛА",
    "complaint.created": "Подана жалоба",
    "complaint.coordinator_review": "Жалоба рассмотрена координатором",
    "complaint.resolved": "Жалоба решена",
    "device.registered": "Зарегистрировано устройство",
    "device.policy_added": "Добавлено правило устройств",
    "device.policy_removed": "Удалено правило устройств",
    "directory.sync": "Синхронизация с доменом",
    "domain_group.mapped": "Группа домена сопоставлена с ролью",
    "drone.registered": "Зарегистрирован БПЛА",
    "drone.published": "Изменена публикация БПЛА",
    "drone.archived": "БПЛА перенесён в архив",
    "drone.unarchived": "БПЛА возвращён из архива",
    "drone.fleet_assigned": "БПЛА закреплён за парком",
    "drone.health_status_changed": "Изменено техническое состояние БПЛА",
    "drone.inspection": "Техосмотр БПЛА",
    "drone.maintenance": "Обслуживание БПЛА",
    "drone.key_issued": "Выдан ключ подключения БПЛА",
    "drone.link_restored": "Связь с БПЛА восстановлена",
    "drone.error": "Ошибка БПЛА",
    "drone.video_check.started": "Начата проверка видео БПЛА",
    "drone.video_source.selected": "Выбран источник видео БПЛА",
    "fleet.created": "Создан парк",
    "fleet.updated": "Изменён парк",
    "map.uploaded": "Загружена карта",
    "map.updated": "Изменена карта",
    "map.deleted": "Удалена карта",
    "map_provider.created": "Добавлен источник карт",
    "map_provider.updated": "Изменён источник карт",
    "map_provider.deleted": "Удалён источник карт",
    "order.created": "Создан заказ",
    "order.status_changed": "Изменён статус заказа",
    "order.validation_failed": "Заказ не прошёл проверку",
    "order.coordinator_assigned": "Назначен координатор заказа",
    "order.coordinator_approved": "Заказ одобрен координатором",
    "order.coordinator_rejected": "Заказ отклонён координатором",
    "order.operator_offered": "Заказ предложен оператору",
    "order.declined": "Оператор отклонил заказ",
    "order.duty_technician": "Сменился техник, ведущий заказ",
    "order.readiness_checked": "Проверена готовность к вылету",
    "order.tech_ready_checked": "Техник подтвердил готовность БПЛА",
    "order.tech_ready_revoked": "Техник отозвал готовность БПЛА",
    "order.cargo_picked_up": "Груз принят к доставке",
    "order.report_submitted": "Отчёт о доставке",
    "order.repeat_requested": "Запрошен повторный рейс",
    "order.repeat_approved": "Повторный рейс одобрен",
    "order.chat.message": "Сообщение в чате заказа",
    "order.comment_photo": "Фото к комментарию заказа",
    "org_unit.created": "Создана группа",
    "org_unit.updated": "Изменена группа",
    "org_unit.deleted": "Удалена группа",
    "retention.run": "Очистка по срокам хранения",
    "score.adjusted": "Скорректированы баллы",
    "score.finalized": "Баллы начислены",
    "scoring.rules_changed": "Изменены правила баллов",
    "support.ticket_created": "Создано обращение в поддержку",
    "support.status_changed": "Изменён статус обращения",
    "support.participant_added": "Добавлен участник обращения",
    "support.attachment_added": "Добавлено вложение к обращению",
    "user.status_changed": "Изменён статус учётной записи",
    "video.captured": "Видео записано",
    "video.stored": "Видео сохранено",
    "video.missing": "Видео отсутствует",
    "video.reviewed": "Видео просмотрено на проверке",
    "video.purged": "Видео удалено по сроку хранения",
    "video.integrity_check": "Проверена целостность видео",
    "video.integrity_failed": "Нарушена целостность видео",
    "video.live.view": "Просмотр видео в реальном времени",
    "video.customer_stream.started": "Заказчику открыта трансляция",
    "video.customer_stream.stopped": "Трансляция заказчику закрыта",
}

ENTITY_LABELS: Dict[str, str] = {
    "order": "Заказ", "user": "Пользователь", "drone": "БПЛА", "drone_model": "Тип БПЛА", "fleet": "Парк",
    "cargo_type": "Вид груза", "consignee": "Грузополучатель", "complaint": "Жалоба", "device": "Устройство",
    "domain_group": "Группа домена", "map": "Карта", "map_provider": "Источник карт", "org_unit": "Группа",
    "scoring_rules": "Правила баллов", "service_zone": "Зона обслуживания", "support_ticket": "Обращение",
}

OUTCOME_LABELS: Dict[str, str] = {"success": "Успешно", "denied": "Отказано", "failure": "Ошибка"}

# Разделы для фильтра журнала: префикс кода действия → подпись.
SECTIONS: Dict[str, str] = {
    "auth.": "Вход и доступ", "user.": "Пользователи", "operator.": "Операторы и квалификация",
    "work.": "Готовность к работе", "order.": "Заказы", "drone.": "БПЛА", "fleet.": "Парки",
    "video.": "Видео", "catalog.": "Справочники", "map": "Карты", "complaint.": "Жалобы",
    "score": "Баллы", "support.": "Поддержка", "device.": "Устройства", "org_unit.": "Группы",
    "directory.": "Домен", "domain_group.": "Домен: группы", "retention.": "Сроки хранения",
    "audit.": "Журнал аудита",
}


def action_label(code: str) -> str:
    return ACTION_LABELS.get(code, code)


def entity_label(code: str) -> str:
    return ENTITY_LABELS.get(code, code)


def outcome_label(code: str) -> str:
    return OUTCOME_LABELS.get(code, code)
