// Конфигурация отдельных клиентов по ролям. Каждый клиент собирается в свой бандл
// (customer/, operator/, coordinator/, technician/, admin/) и содержит только свои страницы —
// код чужих ролей в браузер пользователя не попадает. Доступ всё равно проверяет сервер.

export interface NavItem {
  to: string;        // путь внутри клиента (basename подставляется роутером)
  label: string;
}

export interface AppConfig {
  key: string;
  base: string;      // basename клиента, напр. "/operator"
  title: string;     // заголовок окна и подпись входа
  requiredPerm: string;  // разрешение, подтверждающее роль для этого клиента
  nav: NavItem[];
  hasMonitor: boolean;   // доступен ли монитор полёта (отдельное окно)
}

export const APPS: Record<string, AppConfig> = {
  customer: {
    key: "customer", base: "/customer", title: "FPV Доставка — заказчик", requiredPerm: "order.read_own",
    hasMonitor: false,
    nav: [
      { to: "/", label: "Мои заказы" },
      { to: "/new", label: "Новый заказ" },
      { to: "/drones", label: "Мои дроны" },
    ],
  },
  operator: {
    key: "operator", base: "/operator", title: "FPV Доставка — оператор", requiredPerm: "order.queue.read",
    hasMonitor: true,
    nav: [
      { to: "/", label: "Очередь заказов" },
      { to: "/mine", label: "Мои задания" },
      { to: "/scores", label: "Баллы и рейтинг" },
    ],
  },
  coordinator: {
    key: "coordinator", base: "/coordinator", title: "FPV Доставка — координатор", requiredPerm: "order.note.write",
    hasMonitor: true,
    nav: [{ to: "/", label: "Назначенные заказы" }],
  },
  technician: {
    key: "technician", base: "/technician", title: "FPV Доставка — техник", requiredPerm: "drone.health.write",
    hasMonitor: false,
    nav: [{ to: "/", label: "Парк дронов" }],
  },
  admin: {
    key: "admin", base: "/admin", title: "FPV Доставка — администратор", requiredPerm: "report.read",
    hasMonitor: true,
    nav: [
      { to: "/", label: "Обзор" },
      { to: "/reviews", label: "Проверка выполнения" },
      { to: "/orders", label: "Все заказы" },
      { to: "/drones", label: "Дроны" },
      { to: "/complaints", label: "Жалобы и споры" },
      { to: "/users", label: "Пользователи и роли" },
      { to: "/scoring", label: "Правила баллов" },
      { to: "/maps", label: "Карты" },
      { to: "/audit", label: "Журнал аудита" },
      { to: "/monitoring", label: "Мониторинг" },
    ],
  },
};

// Порядок определяет, какой клиент открыть при входе, если ролей несколько.
const HOME_ORDER = ["admin", "operator", "coordinator", "technician", "customer"];

/** Куда направить вошедшего пользователя по его разрешениям; null — роль ещё не назначена. */
export function roleHome(can: (perm: string) => boolean): string | null {
  for (const key of HOME_ORDER) {
    if (can(APPS[key].requiredPerm)) return `${APPS[key].base}/`;
  }
  return null;
}
