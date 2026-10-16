import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { useLive } from "../realtime";
import type { Note, Order } from "../types";

const NOTE_LABEL: Record<string, string> = {
  remark: "Замечание", message_to_operator: "Сообщение оператору", incident: "Инцидент", deviation: "Отклонение",
};
const ROLE_LABEL: Record<string, string> = {
  customer: "заказчик", operator: "оператор", coordinator: "координатор", admin: "администратор",
};
// После принятия заказа оператором и до завершения разбора — переписка открыта и для заказчика.
const CHAT_STATUSES = ["accepted", "awaiting_readiness", "ready", "in_progress", "awaiting_delivery_confirmation", "disputed"];

/** «Связь по заказу» — общая переписка заказчика, оператора и координатора (администратор видит и
 *  пишет). Служебные замечания и инциденты координатора заказчику не показываются (сервер не отдаёт).
 *  variant: «monitor» — тёмная панель монитора полёта, «card» — блок в карточке заказа. */
export function OrderChat({ order, variant = "card", flying = false }: {
  order: Order; variant?: "monitor" | "card"; flying?: boolean;
}) {
  const { me, can } = useAuth();
  const notes = useLive(() => api.get<{ items: (Note & { author_role?: string })[] }>(`/api/v1/orders/${order.id}/notes`),
    [order.id], (m) => m.kind === "order.note_added" && m.data.order_id === order.id);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = useRef<HTMLDivElement>(null);
  const items = notes.data?.items ?? [];
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [items.length]);

  const isCustomer = order.customer_id === me?.id;
  const isExecutor = order.operator_id === me?.id || order.coordinator_id === me?.id || can("order.read_all");
  const canWrite = can("order.chat") && CHAT_STATUSES.includes(order.status) && (isCustomer || isExecutor);
  const canFrame = isExecutor && flying;

  async function send(e: FormEvent | null, attachFrame = false) {
    e?.preventDefault();
    const body = text.trim() || (attachFrame ? "Кадр с камеры" : "");
    if (!body) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/orders/${order.id}/chat`, { body, attach_frame: attachFrame });
      setText("");
      notes.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const members = [
    order.customer ? `${order.customer.display_name} (заказчик)` : "заказчик",
    order.operator ? `${order.operator.display_name} (оператор)` : "оператор не назначен",
    order.coordinator ? `${order.coordinator.display_name} (координатор)` : "координатор не назначен",
  ];
  const mon = variant === "monitor";
  return (
    <section className={mon ? "mon-panel mon-chat" : "card order-chat"}>
      <div className={mon ? "mon-panel-head" : "card-head"}>
        {mon ? <span>💬 Связь по заказу</span> : <h2>💬 Связь по заказу</h2>}
        <small className={mon ? "" : "muted"} title={members.join(", ")}>{members.join(" · ")}</small>
      </div>
      <div className={mon ? "mon-chat-list" : "order-chat-list"} ref={list}>
        {notes.error ? <p className={mon ? "mon-muted" : "muted"}>{errorText(notes.error)}</p>
          : items.length === 0 && <p className={mon ? "mon-muted" : "muted"}>Сообщений нет</p>}
        {items.map((n) => {
          const mine = n.author?.id === me?.id;
          return (
            <div key={n.id} className={`mon-msg ${mine ? "mine" : ""} sev-${n.severity}`}>
              <div className="mon-msg-head">{mine ? "Вы" : n.author?.display_name ?? "—"}
                {!mine && n.author_role && <em> · {ROLE_LABEL[n.author_role] ?? n.author_role}</em>}
                {n.kind !== "chat" && <em> · {NOTE_LABEL[n.kind] ?? n.kind}</em>}
                <time>{new Date(n.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time></div>
              <div className="mon-msg-body">{n.body}</div>
              {n.attachment_url && <a href={n.attachment_url} target="_blank" rel="noreferrer"><img src={n.attachment_url} alt="Кадр с камеры дрона" /></a>}
            </div>
          );
        })}
      </div>
      {canWrite ? (
        <form className="mon-chat-form" onSubmit={(e) => send(e)}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Написать сообщение…" maxLength={2000} disabled={busy}
                 aria-label="Сообщение по заказу" />
          {isExecutor && (
            <button type="button" className="mon-btn" title="Отправить кадр с камеры дрона" aria-label="Отправить кадр с камеры дрона" disabled={busy || !canFrame}
                    onClick={() => send(null, true)}>📷</button>
          )}
          <button className="mon-btn primary" title="Отправить" aria-label="Отправить" disabled={busy || !text.trim()}>➤</button>
        </form>
      ) : (
        <small className={mon ? "mon-muted" : "muted"}>
          {CHAT_STATUSES.includes(order.status) ? "Писать могут участники заказа." : "Связь по заказу открывается после того, как оператор примет заказ."}
        </small>
      )}
      {error && <small className={mon ? "mon-tone-bad" : "tone-danger"}>{error}</small>}
    </section>
  );
}
