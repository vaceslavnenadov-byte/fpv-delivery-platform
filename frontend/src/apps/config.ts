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
  hasOrders: boolean;    // есть ли страница заказа (/orders/:id) — для ссылок из уведомлений
}

export const APPS: Record<string, AppConfig> = {
  customer: {
    key: "customer", base: "/customer", title: "FPV Доставка — заказчик", requiredPerm: "order.read_own",
    hasOrders: true,
    hasMonitor: false,
    nav: [
      { to: "/", label: "Мои заказы" },
      { to: "/new", label: "Новый заказ" },
    ],
  },
  operator: {
    key: "operator", base: "/operator", title: "FPV Доставка — оператор", requiredPerm: "order.queue.read",
    hasOrders: true,
    hasMonitor: true,
    nav: [
      { to: "/", label: "Очередь заказов" },
      { to: "/mine", label: "Мои задания" },
      { to: "/scores", label: "Баллы и рейтинг" },
    ],
  },
  coordinator: {
    key: "coordinator", base: "/coordinator", title: "FPV Доставка — координатор", requiredPerm: "order.note.write",
    hasOrders: true,
    hasMonitor: true,
    nav: [{ to: "/", label: "Назначенные заказы" }],
  },
  technician: {
    key: "technician", base: "/technician", title: "FPV Доставка — техник", requiredPerm: "drone.health.write",
    hasOrders: true,
    hasMonitor: false,
    nav: [
      { to: "/", label: "Мой парк" },
      { to: "/orders", label: "Заказы на дронах парка" },
      { to: "/service", label: "Обслуживание" },
    ],
  },
  admin: {
    key: "admin", base: "/admin", title: "FPV Доставка — администратор", requiredPerm: "report.read",
    hasOrders: true,
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

/** Клиенты, доступные пользователю (для перехода между разделами при нескольких ролях). */
export function accessibleApps(can: (perm: string) => boolean): AppConfig[] {
  return HOME_ORDER.map((k) => APPS[k]).filter((a) => can(a.requiredPerm));
}

/** Адрес возврата после входа: только путь внутри доступного пользователю клиента
 *  (защита от перенаправления на сторонние сайты). Иначе — null. */
export function safeNext(next: string | null, can: (perm: string) => boolean): string | null {
  if (!next || !/^\/[A-Za-z0-9/_\-.?=&%]*$/.test(next) || next.startsWith("//")) return null;
  const m = next.match(/^\/(customer|operator|coordinator|technician|admin)(\/|$)/);
  if (!m) return null;
  return can(APPS[m[1]].requiredPerm) ? (m[2] ? next : `${next}/`) : null;
}
