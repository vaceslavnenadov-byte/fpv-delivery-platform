import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router";
import { api } from "../api";
import { accessibleApps, type AppConfig } from "../apps/config";
import { useAuth } from "../auth";
import { ROLE_LABELS, fmtDate } from "../labels";
import { realtime, useLive, useRealtimeStatus } from "../realtime";
import { ThemeSwitch } from "../theme";
import type { Notification } from "../types";

// Каркас клиента: боковое меню только своей роли, шапка, уведомления.
// Навигация приходит из конфигурации клиента (src/apps/config.ts).
export function Layout({ app, children }: { app: AppConfig; children: ReactNode }) {
  const { me, logout, can } = useAuth();
  const online = useRealtimeStatus();
  const [panel, setPanel] = useState(false);
  const notes = useLive(() => api.get<{ unread: number; items: Notification[] }>("/api/v1/me/notifications"), [],
    (m) => m.kind === "notification.created");

  useEffect(() => {
    realtime.start();
    return () => realtime.stop();
  }, []);

  if (!me) return null;
  const others = accessibleApps(can).filter((a) => a.key !== app.key);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="brand-mark">◆</span> FPV Доставка</Link>
        <nav>
          <div className="nav-section">
            <div className="nav-title">{app.title.replace("FPV Доставка — ", "")}</div>
            {app.nav.map((i) => (
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
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="muted small">
            <span className={`dot ${online ? "ok" : "off"}`} /> {online ? "Обновления в реальном времени" : "Переподключение…"}
          </div>
          <div className="topbar-right">
            <ThemeSwitch />
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
