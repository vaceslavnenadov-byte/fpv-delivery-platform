import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router";
import { api } from "../api";
import { accessibleApps, type AppConfig } from "../apps/config";
import { useAuth } from "../auth";
import { ROLE_LABELS, fmtDate } from "../labels";
import { realtime, useLive, useRealtimeStatus } from "../realtime";
import { ThemeSwitch } from "../theme";
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

// Каркас клиента: боковое меню только своей роли, шапка, уведомления.
// Навигация приходит из конфигурации клиента (src/apps/config.ts).
export function Layout({ app, children }: { app: AppConfig; children: ReactNode }) {
  const { me, logout, can } = useAuth();
  const online = useRealtimeStatus();
  const [panel, setPanel] = useState(false);
  // Левое меню скрывается кнопкой в левом верхнем углу (выбор запоминается в этом браузере).
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleMenu = () => setCollapsed((c) => { saveCollapsed(!c); return !c; });
  const notes = useLive(() => api.get<{ unread: number; items: Notification[] }>("/api/v1/me/notifications"), [],
    (m) => m.kind === "notification.created");

  useEffect(() => {
    realtime.start();
    return () => realtime.stop();
  }, []);

  if (!me) return null;
  const suspended = me.account_status === "suspended" || me.profile_verified === false;
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
          <div className="nav-section">
            <div className="nav-title">{app.title.replace("FPV Доставка — ", "")}</div>
            {!suspended && app.nav.map((i) => (
              <NavLink key={i.to} to={i.to} end className={({ isActive }) => (isActive ? "active" : "")}>
                {i.label}
              </NavLink>
            ))}
          </div>
          {others.length > 0 && (
            <div className="nav-section">
              <div className="nav-title">Другие разделы</div>
              {/* другой клиент — отдельная страница (свой бандл), поэтому обычная ссылка */}
              {others.map((a) => <a key={a.key} href={`${a.base}/`}>{a.title.replace("FPV Доставка — ", "")}</a>)}
            </div>
          )}
          <div className="nav-section">
            <div className="nav-title">Профиль</div>
            <NavLink to="/profile">Профиль и допуск</NavLink>
          </div>
          {can("support.use") && (
            <div className="nav-section nav-bottom">
              <div className="nav-title">Техподдержка</div>
              <NavLink to="/support/new">Новое сообщение</NavLink>
              <NavLink to="/support" end>Мои обращения</NavLink>
            </div>
          )}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="muted small">
            <span className={`dot ${online ? "ok" : "off"}`} /> {online ? "Обновления в реальном времени" : "Переподключение…"}
          </div>
          <div className="topbar-right">
            <ThemeSwitch />
            {can("support.use") && <Link to="/support/new" className="btn ghost support-btn" title="Написать в техподдержку">🛟 Техподдержка</Link>}
            <button className="btn ghost" onClick={() => setPanel((v) => !v)} aria-expanded={panel}>
              Уведомления {notes.data && notes.data.unread > 0 && <span className="counter">{notes.data.unread}</span>}
            </button>
            <div className="who">
              <strong>{me.display_name}</strong>
              <span className="muted small">{me.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")} · {me.username}</span>
            </div>
            <button className="btn ghost" onClick={async () => { await logout(); window.location.href = "/"; }}>Выйти</button>
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
