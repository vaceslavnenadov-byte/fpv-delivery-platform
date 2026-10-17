import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { Alert, Card, Empty, Pending, Tone } from "../../components/ui";
import { ROLE_LABELS, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import type { Order } from "../../types";
import { ACCEPT, REASONS, STATUS_TONE, type Ticket, type TicketDetail } from "./common";

async function uploadFile(ticketId: number, file: File, comment = "") {
  const qs = new URLSearchParams({ filename: file.name.slice(0, 200), comment }).toString();
  await api.upload(`/api/v1/support/tickets/${ticketId}/files?${qs}`, file, { "Content-Type": file.type || "application/octet-stream" });
}

/** «Техподдержка» в меню любого пользователя: свои обращения и те, куда его добавили. */
export function SupportListPage() {
  const list = useLive(() => api.get<{ items: Ticket[] }>("/api/v1/support/tickets"), [], (m) => m.kind === "support.updated");
  return (
    <div className="stack">
      <div className="row between">
        <h1>Техподдержка</h1>
        <Link className="btn primary" to="/support/new">+ Новое обращение</Link>
      </div>
      <Card title="Мои обращения и переписки">
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Обращений нет</Empty> : <TicketTable items={list.data.items} />}
      </Card>
    </div>
  );
}

function TicketTable({ items, showAuthor = false }: { items: Ticket[]; showAuthor?: boolean }) {
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>№</th><th>Тема</th><th>Причина</th>{showAuthor && <th>Отправитель</th>}<th>Статус</th><th>Поступило</th><th>Последнее сообщение</th></tr></thead>
      <tbody>{items.map((t) => (
        <tr key={t.id} className={t.unread ? "unread-row" : ""}>
          <td><Link to={`/support/${t.id}`}>№{t.id}</Link></td>
          <td><Link to={`/support/${t.id}`}>{t.subject}</Link>{t.unread && <span className="counter">новое</span>}
            {t.order && <div className="muted small">заказ №{t.order.number}</div>}</td>
          <td className="small">{t.reason_label}</td>
          {showAuthor && <td className="small">{t.author?.display_name}<div className="muted">{t.author?.username}</div></td>}
          <td><Tone tone={STATUS_TONE[t.status] ?? "muted"}>{t.status_label}</Tone></td>
          <td className="small">{fmtDate(t.created_at)}</td>
          <td className="small">{fmtDate(t.last_message_at)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

/** Новое обращение: причина, тема, сообщение, фото/видео. */
export function SupportNewPage() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [reason, setReason] = useState("service");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [orderId, setOrderId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const orders = useLive(() => (can("order.read_own") || can("order.read_assigned") || can("order.read_fleet") || can("order.read_all")
    ? api.get<{ items: Order[] }>("/api/v1/orders") : Promise.resolve({ items: [] as Order[] })), []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const t = await api.post<Ticket>("/api/v1/support/tickets", { reason, subject, body, order_id: reason === "order" && orderId ? orderId : null });
      for (const f of files) await uploadFile(t.id, f);
      navigate(`/support/${t.id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="stack">
      <h1>Обращение в техподдержку</h1>
      <Card>
        <form className="stack" onSubmit={submit}>
          <div className="form grid2">
            <label>Причина обращения
              <select value={reason} onChange={(e) => setReason(e.target.value)}>
                {REASONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            {reason === "order" && (
              <label>Заказ
                <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                  <option value="">— не выбран —</option>
                  {(orders.data?.items ?? []).map((o) => <option key={o.id} value={o.id}>№{o.number} — {o.status_label}</option>)}
                </select>
              </label>
            )}
            <label className="span2">Тема<input value={subject} onChange={(e) => setSubject(e.target.value)} required minLength={3} maxLength={200} /></label>
            <label className="span2">Сообщение<textarea value={body} onChange={(e) => setBody(e.target.value)} maxLength={5000} rows={5}
                                                      placeholder="Опишите, что произошло" /></label>
            <label className="span2">Фото и видео (по желанию)
              <input type="file" accept={ACCEPT} multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
              <span className="muted small">Фото — PNG, JPEG, WebP до 15 МБ; видео — MP4, MOV, WebM до 50 МБ.</span>
            </label>
          </div>
          {error && <Alert>{error}</Alert>}
          <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Отправка…" : "Отправить администратору"}</button>
            <Link to="/support" className="btn ghost">Отмена</Link></div>
        </form>
      </Card>
    </div>
  );
}

/** Обращение — чат с администратором и добавленными участниками. */
export function SupportTicketPage() {
  const { id = "" } = useParams();
  const { me, can } = useAuth();
  const t = useLive(() => api.get<TicketDetail>(`/api/v1/support/tickets/${id}`), [id],
    (m) => m.kind === "support.updated" && String(m.data.ticket_id) === id);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [find, setFind] = useState("");
  const [found, setFound] = useState<{ id: string; display_name: string; username: string }[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const manage = can("support.manage");
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [t.data?.messages.length]);
  useEffect(() => {
    if (!manage || find.trim().length < 2) { setFound([]); return; }
    const h = window.setTimeout(() => api.get<{ items: typeof found }>(`/api/v1/support/users?q=${encodeURIComponent(find)}`)
      .then((r) => setFound(r.items), () => setFound([])), 300);
    return () => window.clearTimeout(h);
  }, [find, manage]);
  if (t.error) return <Alert>{errorText(t.error)}</Alert>;
  if (!t.data) return <Pending />;
  const d = t.data;
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      t.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const send = (e: FormEvent) => { e.preventDefault(); if (text.trim()) void run(() => api.post(`/api/v1/support/tickets/${d.id}/messages`, { body: text })).then(() => setText("")); };
  const participants = new Set(d.participants.map((x) => x.id));
  return (
    <div className="stack">
      <div className="row between">
        <div>
          <h1>№{d.id}. {d.subject} <Tone tone={STATUS_TONE[d.status] ?? "muted"}>{d.status_label}</Tone></h1>
          <div className="muted small">{d.reason_label} · {d.author?.display_name}, {fmtDate(d.created_at)}
            {d.order && <> · заказ <Link to={`/orders/${d.order.id}`}>№{d.order.number}</Link></>}
            {d.status_changed_by && <> · статус: {d.status_changed_by.display_name}, {fmtDate(d.status_changed_at)}</>}</div>
        </div>
        <Link className="btn ghost" to={manage ? "/support-admin" : "/support"}>← К списку</Link>
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="grid2 support-grid">
        <Card title="Переписка">
          <div className="support-chat" ref={listRef}>
            {d.messages.length === 0 && <p className="muted">Сообщений пока нет</p>}
            {d.messages.map((m) => m.system ? (
              <div key={m.id} className="support-system">{m.body} · {fmtDate(m.created_at)}</div>
            ) : (
              <div key={m.id} className={`support-msg ${m.author?.id === me?.id ? "mine" : ""}`}>
                <div className="support-msg-head"><b>{m.author?.id === me?.id ? "Вы" : m.author?.display_name ?? "—"}</b>
                  <time>{fmtDate(m.created_at)}</time></div>
                {m.body && <div className="support-msg-body">{m.body}</div>}
                {m.attachments.map((a) => a.content_type.startsWith("video/")
                  ? <video key={a.id} src={a.url} controls preload="metadata" className="support-media" />
                  : <a key={a.id} href={a.url} target="_blank" rel="noreferrer"><img src={a.url} alt={a.filename || "фото"} className="support-media" /></a>)}
              </div>
            ))}
          </div>
          <form className="support-compose" onSubmit={send}>
            <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Сообщение…" maxLength={5000} rows={2}
                      aria-label="Сообщение в обращении" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }} />
            <input ref={fileRef} type="file" accept={ACCEPT} hidden multiple onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) void run(async () => { for (const f of files) await uploadFile(d.id, f, text.trim()); setText(""); });
              e.target.value = "";
            }} />
            <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()} aria-label="Приложить фото или видео">📎 Фото/видео</button>
            <button className="btn primary" disabled={busy || !text.trim()}>Отправить</button>
          </form>
        </Card>
        <div className="stack">
          {manage && (
            <Card title="Статус обращения">
              <div className="row wrap">
                {([["in_progress", "В работе"], ["rejected", "Отклонено"], ["done", "Выполнено"]] as const).map(([k, v]) => (
                  <button key={k} className={`btn ${d.status === k ? "primary" : ""}`} disabled={busy || d.status === k}
                          onClick={() => void run(() => api.post(`/api/v1/support/tickets/${d.id}/status`, { status: k }))}>{v}</button>
                ))}
              </div>
              <p className="muted small">Статус видят все участники переписки.</p>
            </Card>
          )}
          <Card title={`Участники (${d.participants.length})`}>
            <ul className="support-people">
              {d.participants.map((x) => (
                <li key={x.id}><b>{x.display_name}</b> <span className="muted small">{x.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}</span>
                  {x.added_by && x.added_by.id !== x.id && <div className="muted small">добавил(а) {x.added_by.display_name}</div>}</li>
              ))}
            </ul>
            {manage && (
              <div className="stack">
                <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Добавить в обсуждение: имя или логин"
                       aria-label="Поиск пользователя для добавления" />
                {found.filter((u) => !participants.has(u.id)).map((u) => (
                  <button key={u.id} className="btn small" disabled={busy}
                          onClick={() => void run(() => api.post(`/api/v1/support/tickets/${d.id}/participants`, { user_id: u.id })).then(() => setFind(""))}>
                    + {u.display_name} ({u.username})</button>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

/** «Обращения в ТП» (администратор): все обращения с фильтрами. */
export function AdminSupportPage() {
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [q, setQ] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const qs = new URLSearchParams(Object.entries({ status, reason, q, date_from: from, date_to: to }).filter(([, v]) => v)).toString();
  const list = useLive(() => api.get<{ items: Ticket[]; counts: Record<string, number> }>(`/api/v1/support/admin/tickets${qs ? `?${qs}` : ""}`),
    [qs], (m) => m.kind === "support.updated");
  const c = list.data?.counts ?? {};
  return (
    <div className="stack">
      <h1>Обращения в ТП</h1>
      <div className="row wrap support-counts">
        {[["", "Все"], ["new", "Новые"], ["in_progress", "В работе"], ["rejected", "Отклонено"], ["done", "Выполнено"]].map(([k, v]) => (
          <button key={k} className={`btn small ${status === k ? "primary" : ""}`} onClick={() => setStatus(k)}>
            {v}{k ? ` (${c[k] ?? 0})` : ""}</button>
        ))}
      </div>
      <Card actions={<div className="row wrap filters">
        <input placeholder="Поиск: тема или отправитель" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск обращений" />
        <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Причина">
          <option value="">Все причины</option>{REASONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label className="row">с <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Дата с" /></label>
        <label className="row">по <input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дата по" /></label>
      </div>}>
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Обращений нет</Empty> : <TicketTable items={list.data.items} showAuthor />}
      </Card>
    </div>
  );
}
