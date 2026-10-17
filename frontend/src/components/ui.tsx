import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
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

/** Всплывающее окно поверх страницы. Закрывается кнопками окна или клавишей Esc. */
export function Modal({ title, onClose, children, footer, wide }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal card ${wide ? "wide" : ""}`} role="dialog" aria-modal="true">
        <header className="card-head"><h2>{title}</h2></header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>, document.body);
}

/** Картинка в сообщении: по нажатию открывается поверх страницы, кнопка «Закрыть» возвращает к чату. */
export function ChatImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <>
      <button type="button" className="img-thumb" onClick={() => setOpen(true)} title="Открыть">
        <img src={src} alt={alt} className={className} />
      </button>
      {open && createPortal(
        <div className="lightbox" role="dialog" aria-modal="true" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <img src={src} alt={alt} />
          <button type="button" className="btn primary lightbox-close" onClick={() => setOpen(false)}>Закрыть</button>
        </div>, document.body)}
    </>
  );
}

/** Сортировка таблицы по столбцу: щелчок по заголовку меняет порядок. */
export function useSort<T>(rows: T[], initial: keyof T & string) {
  const [key, setKey] = useState<string>(initial);
  const [asc, setAsc] = useState(true);
  const sorted = [...rows].sort((a, b) => {
    const x = (a as Record<string, unknown>)[key], y = (b as Record<string, unknown>)[key];
    const r = typeof x === "number" && typeof y === "number" ? x - y
      : String(x ?? "").localeCompare(String(y ?? ""), "ru", { numeric: true });
    return asc ? r : -r;
  });
  const th = (k: keyof T & string, label: ReactNode, num = false) => (
    <th className={`sortable ${num ? "num" : ""}`} onClick={() => { if (k === key) setAsc(!asc); else { setKey(k); setAsc(true); } }}
        aria-sort={k === key ? (asc ? "ascending" : "descending") : "none"}>
      {label}{k === key ? (asc ? " ▲" : " ▼") : ""}
    </th>
  );
  return { sorted, th };
}
