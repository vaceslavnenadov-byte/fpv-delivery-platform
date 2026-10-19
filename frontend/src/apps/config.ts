// Конфигурация отдельных клиентов по ролям. Каждый клиент собирается в свой бандл
// (customer/, operator/, coordinator/, technician/, recruiter/, admin/) и содержит только свои страницы —
// код чужих ролей в браузер пользователя не попадает. Доступ всё равно проверяет сервер.

export interface NavItem {
  to: string;        // путь внутри клиента (basename подставляется роутером)
  label: string;
  section?: string;  // раздел меню (сворачиваемый); без раздела — пункт вверху меню
}

export interface NavGroup {
  title: string | null;
  items: NavItem[];
}

/** Пункты меню по разделам — в порядке первого появления раздела. */
export function navGroups(nav: NavItem[]): NavGroup[] {
  const groups: NavGroup[] = [];
  for (const item of nav) {
    const title = item.section ?? null;
    let g = groups.find((x) => x.title === title);
    if (!g) {
      g = { title, items: [] };
      if (title === null) groups.unshift(g);
      else groups.push(g);
    }
    g.items.push(item);
  }
  return groups;
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
      { to: "/", label: "Мои заказы", section: "Заказы" },
      { to: "/new", label: "Новый заказ", section: "Заказы" },
    ],
  },
  operator: {
    key: "operator", base: "/operator", title: "FPV Доставка — оператор", requiredPerm: "order.queue.read",
    hasOrders: true,
    hasMonitor: true,
    nav: [
      { to: "/", label: "Предложенные заказы", section: "Заказы" },
      { to: "/mine", label: "Мои задания", section: "Заказы" },
      { to: "/qualification", label: "Квалификация", section: "Допуск" },
      { to: "/scores", label: "Баллы и рейтинг", section: "Результаты" },
    ],
  },
  coordinator: {
    key: "coordinator", base: "/coordinator", title: "FPV Доставка — координатор", requiredPerm: "order.note.write",
    hasOrders: true,
    hasMonitor: true,
    nav: [
      { to: "/", label: "Видеостена", section: "Наблюдение" },
      { to: "/map", label: "Карта заказов", section: "Наблюдение" },
      { to: "/orders", label: "Назначенные заказы", section: "Заказы" },
      { to: "/reviews", label: "Проверка выполнения", section: "Заказы" },
    ],
  },
  technician: {
    key: "technician", base: "/technician", title: "FPV Доставка — техник", requiredPerm: "drone.health.write",
    hasOrders: true,
    hasMonitor: false,
    nav: [
      { to: "/", label: "Мой парк", section: "Парк БПЛА" },
      { to: "/orders", label: "Заказы на дронах парка", section: "Заказы" },
      { to: "/service", label: "Обслуживание", section: "Парк БПЛА" },
      { to: "/archive", label: "Архив БПЛА", section: "Парк БПЛА" },
      { to: "/tech-report", label: "Отчёт о ТехСостоянии", section: "Отчёты" },
    ],
  },
  recruiter: {
    key: "recruiter", base: "/recruiter", title: "FPV Доставка — рекрутер", requiredPerm: "user.recruit",
    hasOrders: false,
    hasMonitor: false,
    nav: [
      { to: "/", label: "Пользователи", section: "Пользователи" },
      { to: "/org-units", label: "Группы пользователей", section: "Пользователи" },
      { to: "/profiles", label: "Проверка профилей", section: "Проверки" },
      { to: "/operators", label: "Допуск операторов", section: "Проверки" },
      { to: "/qualification", label: "Подтверждение квалификации", section: "Проверки" },
    ],
  },
  admin: {
    key: "admin", base: "/admin", title: "FPV Доставка — администратор", requiredPerm: "report.read",
    hasOrders: true,
    hasMonitor: true,
    nav: [
      { to: "/", label: "Обзор" },
      { to: "/map", label: "Карта заказов", section: "Заказы" },
      { to: "/orders", label: "Все заказы", section: "Заказы" },
      { to: "/complaints", label: "Жалобы и споры", section: "Заказы" },
      { to: "/drones", label: "Дроны", section: "Парк и территория" },
      { to: "/service-zones", label: "Зоны обслуживания", section: "Парк и территория" },
      { to: "/maps", label: "Карты", section: "Парк и территория" },
      { to: "/people", label: "Пользователи", section: "Пользователи и доступ" },
      { to: "/org-units", label: "Группы пользователей", section: "Пользователи и доступ" },
      { to: "/profiles", label: "Проверка профилей", section: "Пользователи и доступ" },
      { to: "/operators", label: "Допуск операторов", section: "Пользователи и доступ" },
      { to: "/qualification", label: "Подтверждение квалификации", section: "Пользователи и доступ" },
      { to: "/users", label: "Домен, операторы, устройства", section: "Пользователи и доступ" },
      { to: "/cargo-types", label: "Типы грузов", section: "Справочники" },
      { to: "/consignees", label: "Типы грузополучателей", section: "Справочники" },
      { to: "/drone-models", label: "Типы БПЛА", section: "Справочники" },
      { to: "/scoring", label: "Правила баллов", section: "Справочники" },
      { to: "/support-admin", label: "Обращения в ТП", section: "Контроль" },
      { to: "/audit", label: "Журнал аудита", section: "Контроль" },
      { to: "/monitoring", label: "Мониторинг", section: "Контроль" },
    ],
  },
};

// Порядок определяет, какой клиент открыть при входе, если ролей несколько.
const HOME_ORDER = ["admin", "recruiter", "operator", "coordinator", "technician", "customer"];

/** Куда направить вошедшего пользователя по его разрешениям; null — роль ещё не назначена. */
export function roleHome(can: (perm: string) => boolean): string | null {
  for (const key of HOME_ORDER) {
    if (can(APPS[key].requiredPerm)) return `${APPS[key].base}/`;
  }
  return null;
}

/** Приостановленная учётная запись: прав роли нет, но раздел своей роли открывается с профилем и
 *  техподдержкой («Новое обращение», «Мои обращения»). */
export function suspendedApp(roles: string[]): AppConfig {
  const key = HOME_ORDER.find((k) => roles.includes(k)) ?? "customer";
  return APPS[key];
}

/** Клиенты, доступные пользователю (для перехода между разделами при нескольких ролях). */
export function accessibleApps(can: (perm: string) => boolean): AppConfig[] {
  return HOME_ORDER.map((k) => APPS[k]).filter((a) => can(a.requiredPerm));
}

/** Адрес возврата после входа: только путь внутри доступного пользователю клиента
 *  (защита от перенаправления на сторонние сайты). Иначе — null. */
export function safeNext(next: string | null, can: (perm: string) => boolean): string | null {
  if (!next || !/^\/[A-Za-z0-9/_\-.?=&%]*$/.test(next) || next.startsWith("//")) return null;
  const m = next.match(/^\/(customer|operator|coordinator|technician|recruiter|admin)(\/|$)/);
  if (!m) return null;
  return can(APPS[m[1]].requiredPerm) ? (m[2] ? next : `${next}/`) : null;
}
