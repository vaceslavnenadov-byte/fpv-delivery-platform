import { ORDER_STATUS, fmtDate } from "../labels";
import type { HistoryItem } from "../types";

export function Timeline({ items }: { items: HistoryItem[] }) {
  return (
    <ol className="timeline">
      {items.map((h) => (
        <li key={h.id}>
          <div className="tl-dot" />
          <div>
            <strong>{ORDER_STATUS[h.to]?.label ?? h.to}</strong>
            <div className="muted small">
              {fmtDate(h.created_at)} · {h.actor ?? h.actor_kind}
              {h.actor_kind !== "user" && ` (${h.actor_kind === "system" ? "система" : "дрон"})`}
            </div>
            {h.reason && <div className="small">{h.reason}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
