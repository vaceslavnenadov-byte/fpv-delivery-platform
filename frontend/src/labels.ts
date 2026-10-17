export const ORDER_STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: "Черновик", tone: "muted" },
  pending_confirmation: { label: "Ожидает подтверждения", tone: "info" },
  awaiting_operator: { label: "Ожидает назначения оператора", tone: "info" },
  accepted: { label: "Принят оператором", tone: "info" },
  awaiting_readiness: { label: "Ожидает проверки готовности", tone: "warn" },
  ready: { label: "Готов к выполнению", tone: "ok" },
  in_progress: { label: "Выполняется", tone: "accent" },
  awaiting_delivery_confirmation: { label: "Ожидает подтверждения доставки", tone: "info" },
  admin_review: { label: "На проверке администратора", tone: "warn" },
  completed: { label: "Выполнен", tone: "ok" },
  rejected: { label: "Отклонён", tone: "danger" },
  cancelled: { label: "Отменён", tone: "muted" },
  aborted_technical: { label: "Прерван по техническим причинам", tone: "danger" },
  disputed: { label: "Передан на рассмотрение спора", tone: "danger" },
};

export const DRONE_STATUS: Record<string, { label: string; tone: string }> = {
  pending_approval: { label: "Ожидает подтверждения", tone: "warn" },
  active: { label: "Регистрация подтверждена", tone: "ok" },
  blocked: { label: "Заблокирован", tone: "danger" },
  retired: { label: "Выведен из эксплуатации", tone: "muted" },
};

export const HEALTH_STATUS: Record<string, { label: string; tone: string }> = {
  serviceable: { label: "Исправен", tone: "ok" },
  requires_check: { label: "Требует проверки", tone: "warn" },
  grounded: { label: "Не допущен к эксплуатации", tone: "danger" },
};

export const LINK_STATUS: Record<string, { label: string; tone: string }> = {
  online: { label: "На связи", tone: "ok" },
  degraded: { label: "Связь нестабильна", tone: "warn" },
  lost: { label: "Связь потеряна", tone: "danger" },
  offline: { label: "Не подключён", tone: "muted" },
};

export const CARGO_TYPES: Record<string, string> = {
  documents: "Документы",
  parcel: "Посылка",
  food: "Продукты питания",
  medical_supplies: "Медицинские изделия",
  spare_parts: "Запчасти",
};

/** Название типа груза заказа (из справочника «Типы грузов»). */
export function cargoName(o: { cargo_type: string; cargo_type_name?: string }): string {
  return o.cargo_type_name ?? CARGO_TYPES[o.cargo_type] ?? o.cargo_type;
}

export const ARCHIVE_REASONS: Record<string, string> = {
  broken: "Сломан",
  lost: "Утерян",
  repair: "В ремонте",
  returned: "Возвращён производителю",
  worn_out: "Отработал",
  dismantled: "Разобран",
  obsolete: "Устарел",
  peripherals: "Поломка/отсутствие периферийного оборудования",
  other: "Другое",
};

export const ROLE_LABELS: Record<string, string> = {
  customer: "Заказчик",
  operator: "Оператор",
  coordinator: "Координатор",
  technician: "Техник",
  recruiter: "Рекрутер",
  admin: "Администратор",
};

export const SCORE_ITEMS: Record<string, string> = {
  receipt_confirmed: "Подтверждение получения",
  report_submitted: "Отчёт о выполнении",
  video_verified: "Проверенное видеоподтверждение",
  admin_approved: "Подтверждение администратора",
};

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export function fmtDistance(m: number | undefined | null): string {
  if (m == null) return "—";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} км` : `${m} м`;
}
