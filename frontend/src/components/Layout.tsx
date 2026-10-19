import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { api } from "../api";
import { accessibleApps, navGroups, type AppConfig } from "../apps/config";
import { useAuth } from "../auth";
import { ROLE_LABELS, fmtDate } from "../labels";
import { realtime, useLive, useRealtimeStatus } from "../realtime";
import { ThemeSwitch } from "../theme";
import { ReadinessSwitch, type WorkRole } from "./Readiness";
import type { Notification } from "../types";

const MENU_PREF = "fpv.menuCollapsed";
function readCollapsed(): boolean {
  try {
    const v = localStorage.getItem(MENU_PREF);
    return v === null ? window.innerWidth < 720 : v === "1";
  } catch {
    return false;
  }
}
function saveCollapsed(v: boolean): void {
  try { localStorage.setItem(MENU_PREF, v ? "1" : "0"); } catch { /* до перезагрузки */ }
}

// Свёрнутые разделы меню (по клиенту), запоминаются в браузере.
const SECTIONS_PREF = "fpv.navClosed.";
function readClosed(app: string): string[] {
  try { return JSON.parse(localStorage.getItem(SECTIONS_PREF + app) ?? "[]"); } catch { return []; }
}
function saveClosed(app: string, v: string[]): void {
  try { localStorage.setItem(SECTIONS_PREF + app, JSON.stringify(v)); } catch { /* до перезагрузки */ }
}

/** Раздел меню: заголовок сворачивает и разворачивает пункты. Раздел с открытой страницей
 *  показывает её пункт и в свёрнутом виде, чтобы было видно, где вы находитесь. */
function NavSection({ id, title, closed, onToggle, children, activeChild, className = "" }: {
  id: string; title: string; closed: boolean; onToggle: () => void; children: ReactNode; activeChild?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`nav-section ${closed ? "closed" : ""} ${className}`}>
      <button type="button" className="nav-title nav-toggle" onClick={onToggle} aria-expanded={!closed} aria-controls={`nav-${id}`}>
        <span className="nav-caret" aria-hidden>{closed ? "▸" : "▾"}</span>{title}
      </button>
      <div id={`nav-${id}`} className="nav-items">{closed ? activeChild : children}</div>
    </div>
  );
}

// Каркас клиента: боковое меню только своей роли (по разделам), шапка, уведомления.
// Навигация приходит из конфигурации клиента (src/apps/config.ts).
export function Layout({ app, children }: { app: AppConfig; children: ReactNode }) {
  const { me, logout, can } = useAuth();
  const online = useRealtimeStatus();
  const [panel, setPanel] = useState(false);
  // Левое меню скрывается кнопкой в левом верхнем углу (выбор запоминается в этом браузере).
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleMenu = () => setCollapsed((c) => { saveCollapsed(!c); return !c; });
  const [closed, setClosed] = useState<string[]>(() => readClosed(app.key));
  const { pathname } = useLocation();
  const toggleSection = (t: string) => setClosed((c) => {
    const next = c.includes(t) ? c.filter((x) => x !== t) : [...c, t];
    saveClosed(app.key, next);
    return next;
  });
  const notes = useLive(() => api.get<{ unread: number; items: Notification[] }>("/api/v1/me/notifications"), [],
    (m) => m.kind === "notification.created");

  useEffect(() => {
    realtime.start();
    return () => realtime.stop();
  }, []);

  if (!me) return null;
  const suspended = me.account_status === "suspended" || me.profile_verified === false;
  // «Готов / Не готов к работе» — в клиентах оператора, координатора и техника, для своей роли.
  const workRole = (["operator", "coordinator", "technician"] as WorkRole[])
    .find((r) => r === app.key && me.roles.includes(r) && can("work.readiness") && !suspended) ?? null;
  // Рекрутёрский раздел администратору не нужен: его страницы — в меню администратора.
  const others = suspended ? [] : accessibleApps(can).filter((a) => a.key !== app.key && !(app.key === "admin" && a.key === "recruiter"));

  return (
    <div className={`shell ${collapsed ? "menu-collapsed" : ""}`}>
      <button type="button" className="menu-toggle" onClick={toggleMenu} aria-expanded={!collapsed} aria-controls="side-menu"
              title={collapsed ? "Показать меню" : "Скрыть меню"} aria-label={collapsed ? "Показать меню" : "Скрыть меню"}>
        {collapsed ? "☰" : "⟨"}
      </button>
      <aside className="sidebar" id="side-menu" hidden={collapsed}>
        <Link to="/" className="brand"><span className="brand-mark">◆</span> FPV Доставка</Link>
        <nav>
          {(() => {
            const groups = suspended ? [] : navGroups(app.nav);
            const link = (i: { to: string; label: string }) => (
              <NavLink key={i.to} to={i.to} end className={({ isActive }) => (isActive ? "active" : "")}>{i.label}</NavLink>);
            const isActive = (to: string) => (to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`));
            const titles = [...groups.filter((g) => g.title).map((g) => g.title!), "Другие разделы", "Профиль", "Техподдержка"];
            const allClosed = titles.every((t) => closed.includes(t));
            const setAll = (close: boolean) => { const v = close ? titles : []; setClosed(v); saveClosed(app.key, v); };
            return (
              <>
                <div className="nav-head">
                  <span className="nav-role">{app.title.replace("FPV Доставка — ", "")}</span>
                  <button type="button" className="nav-all" onClick={() => setAll(!allClosed)}>
                    {allClosed ? "Развернуть все" : "Свернуть все"}</button>
                </div>
                {groups.map((g, gi) => g.title === null
                  ? <div key="top" className="nav-section nav-top">{g.items.map(link)}</div>
                  : (
                    <NavSection key={g.title} id={`s${gi}`} title={g.title} closed={closed.includes(g.title)}
                                onToggle={() => toggleSection(g.title!)}
                                activeChild={g.items.filter((i) => isActive(i.to)).map(link)}>
                      {g.items.map(link)}
                    </NavSection>
                  ))}
                {others.length > 0 && (
                  <NavSection id="other" title="Другие разделы" closed={closed.includes("Другие разделы")}
                              onToggle={() => toggleSection("Другие разделы")}>
                    {/* другой клиент — отдельная страница (свой бандл), поэтому обычная ссылка */}
                    {others.map((a) => <a key={a.key} href={`${a.base}/`}>{a.title.replace("FPV Доставка — ", "")}</a>)}
                  </NavSection>
                )}
                <NavSection id="profile" title="Профиль" closed={closed.includes("Профиль")} onToggle={() => toggleSection("Профиль")}
                            activeChild={pathname.startsWith("/profile") ? <NavLink to="/profile">Профиль и допуск</NavLink> : null}>
                  <NavLink to="/profile">Профиль и допуск</NavLink>
                </NavSection>
                {can("support.use") && (
                  <NavSection id="support" title="Техподдержка" className="nav-bottom" closed={closed.includes("Техподдержка")}
                              onToggle={() => toggleSection("Техподдержка")}
                              activeChild={pathname.startsWith("/support") ? <NavLink to={pathname}>{pathname === "/support/new" ? "Новое сообщение" : "Мои обращения"}</NavLink> : null}>
                    <NavLink to="/support/new">Новое сообщение</NavLink>
                    <NavLink to="/support" end>Мои обращения</NavLink>
                  </NavSection>
                )}
              </>
            );
          })()}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="muted small">
            <span className={`dot ${online ? "ok" : "off"}`} /> {online ? "Обновления в реальном времени" : "Переподключение…"}
          </div>
          <div className="topbar-right">
            {workRole && <ReadinessSwitch role={workRole} />}
            <ThemeSwitch />
            {can("support.use") && <Link to="/support/new" className="btn ghost support-btn" title="Написать в техподдержку">🛟 Техподдержка</Link>}
            <button className="btn ghost" onClick={() => setPanel((v) => !v)} aria-expanded={panel}>
              Уведомления {notes.data && notes.data.unread > 0 && <span className="counter">{notes.data.unread}</span>}
            </button>
            <div className="topbar-user">
              <div className="who">
                <strong>{me.display_name}</strong>
                <span className="muted small">{me.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")} · {me.username}</span>
              </div>
              <button className="btn ghost" onClick={async () => { await logout(); window.location.href = "/"; }}>Выйти</button>
            </div>
          </div>
        </header>
        {me.account_status !== "suspended" && me.profile_verified === false && (
          <div className="alert tone-warn suspended-banner" role="alert">
            Учётная запись не подтверждена: профиль на проверке у администратора или рекрутера. До подтверждения рабочие
            разделы недоступны; доступны профиль, уведомления и техподдержка.
          </div>
        )}
        {me.account_status === "suspended" && (
          <div className="alert tone-warn suspended-banner" role="alert">
            Учётная запись приостановлена администратором: должностные обязанности временно недоступны. Доступны профиль,
            уведомления и обращения в техподдержку.
          </div>
        )}
        {panel && (
          <div className="notif-panel">
            <div className="row between">
              <strong>Уведомления</strong>
              <button className="btn small" onClick={async () => { await api.post("/api/v1/me/notifications/read"); notes.reload(); }}>
                Отметить все прочитанными
              </button>
            </div>
            {(notes.data?.items ?? []).length === 0 && <p className="empty">Уведомлений нет</p>}
            {(notes.data?.items ?? []).map((n) => (
              <div key={n.id} className={`notif ${n.read ? "" : "unread"} sev-${n.severity}`}>
                <div className="row between">
                  <strong>{n.title}</strong>
                  <span className="muted small">{fmtDate(n.created_at)}</span>
                </div>
                {n.body && <div className="small">{n.body}</div>}
                {n.order_id && app.hasOrders && <Link to={`/orders/${n.order_id}`} onClick={() => setPanel(false)} className="small">Открыть заказ</Link>}
              </div>
            ))}
          </div>
        )}
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
