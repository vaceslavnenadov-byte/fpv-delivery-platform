import type { ReactNode } from "react";
import { DRONE_STATUS, HEALTH_STATUS, LINK_STATUS, ORDER_STATUS } from "../labels";

const MAPS: Record<string, Record<string, { label: string; tone: string }>> = {
  order: ORDER_STATUS,
  drone: DRONE_STATUS,
  health: HEALTH_STATUS,
  link: LINK_STATUS,
};

export function Badge({ kind, value }: { kind: "order" | "drone" | "health" | "link"; value: string }) {
  const item = MAPS[kind][value] ?? { label: value, tone: "muted" };
  return <span className={`badge tone-${item.tone}`}>{item.label}</span>;
}

export function Tone({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`badge tone-${tone}`}>{children}</span>;
}

export function Card({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Alert({ tone = "danger", children }: { tone?: string; children: ReactNode }) {
  if (!children) return null;
  return <div className={`alert tone-${tone}`} role="alert">{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Pending({ label = "Загрузка…" }: { label?: string }) {
  return <p className="pending">{label}</p>;
}

/** Пометка для функций, которые сознательно не реализованы в первой версии. */
export function NotImplemented({ children }: { children: ReactNode }) {
  return <div className="not-impl">⚠ Не реализовано в первой версии: {children}</div>;
}
