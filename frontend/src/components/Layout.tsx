import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ROLE_LABELS, fmtDate } from "../labels";
import { realtime, useLive, useRealtimeStatus } from "../realtime";
import type { Notification } from "../types";

interface NavItem {
  to: string;
  label: string;
  perm: string;
}

const NAV: { section: string; items: NavItem[] }[] = [
  { section: "Заказчик", items: [
    { to: "/customer", label: "Мои заказы", perm: "order.read_own" },
    { to: "/customer/new", label: "Новый заказ", perm: "order.create" },
    { to: "/customer/drones", label: "Мои дроны", perm: "drone.read_own" },
  ] },
  { section: "Оператор", items: [
    { to: "/operator", label: "Очередь заказов", perm: "order.queue.read" },
    { to: "/operator/mine", label: "Мои задания", perm: "order.execute" },
    { to: "/operator/scores", label: "Баллы и рейтинг", perm: "score.read_own" },
  ] },
  { section: "Координатор", items: [
    { to: "/coordinator", label: "Назначенные заказы", perm: "order.note.write" },
  ] },
  { section: "Техник", items: [
    { to: "/technician", label: "Парк дронов", perm: "drone.health.write" },
  ] },
  { section: "Администратор", items: [
    { to: "/admin", label: "Обзор", perm: "report.read" },
    { to: "/admin/reviews", label: "Проверка выполнения", perm: "order.review" },
    { to: "/admin/orders", label: "Все заказы", perm: "order.read_all" },
    { to: "/admin/drones", label: "Дроны", perm: "drone.approve" },
    { to: "/admin/complaints", label: "Жалобы и споры", perm: "complaint.resolve" },
    { to: "/admin/users", label: "Пользователи и роли", perm: "user.manage" },
    { to: "/admin/scoring", label: "Правила баллов", perm: "scoring.manage" },
    { to: "/admin/audit", label: "Журнал аудита", perm: "audit.read" },
    { to: "/admin/monitoring", label: "Мониторинг", perm: "system.monitor" },
  ] },
];

export function Layout({ children }: { children: ReactNode }) {
  const { me, logout, can } = useAuth();
  const navigate = useNavigate();
  const online = useRealtimeStatus();
  const [panel, setPanel] = useState(false);
  const notes = useLive(() => api.get<{ unread: number; items: Notification[] }>("/api/v1/me/notifications"), [],
    (m) => m.kind === "notification.created");

  useEffect(() => {
    realtime.start();
    return () => realtime.stop();
  }, []);

  if (!me) return null;
  const sections = NAV.map((s) => ({ ...s, items: s.items.filter((i) => can(i.perm)) })).filter((s) => s.items.length);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="brand-mark">◆</span> FPV Доставка</Link>
        <nav>
          {sections.map((s) => (
            <div key={s.section} className="nav-section">
              <div className="nav-title">{s.section}</div>
              {s.items.map((i) => (
                <NavLink key={i.to} to={i.to} end className={({ isActive }) => (isActive ? "active" : "")}>
                  {i.label}
                </NavLink>
              ))}
            </div>
          ))}
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
            <button className="btn ghost" onClick={() => setPanel((v) => !v)} aria-expanded={panel}>
              Уведомления {notes.data && notes.data.unread > 0 && <span className="counter">{notes.data.unread}</span>}
            </button>
            <div className="who">
              <strong>{me.display_name}</strong>
              <span className="muted small">{me.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")} · {me.username}</span>
            </div>
            <button className="btn ghost" onClick={async () => { await logout(); navigate("/login"); }}>Выйти</button>
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
                {n.order_id && <Link to={`/orders/${n.order_id}`} onClick={() => setPanel(false)} className="small">Открыть заказ</Link>}
              </div>
            ))}
          </div>
        )}
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
