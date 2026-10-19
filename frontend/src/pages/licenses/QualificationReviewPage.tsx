import { useState } from "react";
import { api, errorText } from "../../api";
import { Alert, Card, Empty, Modal, Pending, useSort } from "../../components/ui";
import { useLive } from "../../realtime";
import { LICENSE_STATUS, LicenseCardBody, fmtDay, type License } from "./common";

// Администратор и рекрутёр: «Подтверждение квалификации» — заявки операторов на допуск к типам БПЛА.
// Статус можно сменить и после согласования — прямо в списке.

export function QualificationReviewPage() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const list = useLive(() => api.get<{ items: License[] }>(`/api/v1/licenses${status ? `?status=${status}` : ""}`), [status],
    (m) => m.kind === "operator.license" || m.kind === "notification.created");
  const [open, setOpen] = useState<License | null>(null);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const needle = q.trim().toLowerCase();
  const rows = (list.data?.items ?? [])
    .filter((l) => !needle || [l.full_name, l.model, l.manufacturer, l.license_number, l.issued_by].join(" ").toLowerCase().includes(needle))
    .map((l) => ({ ...l, model_name: `${l.manufacturer} ${l.model}`, status_rank: { pending: 0, fix: 1, approved: 2, rejected: 3 }[l.status] }));
  const { sorted, th } = useSort(rows, "status_rank");

  async function decide(l: License, s: string, comment = "") {
    setMsg(null);
    try {
      await api.post(`/api/v1/licenses/${l.id}/decision`, { status: s, comment });
      setMsg({ tone: "ok", text: `${l.short_name}, ${l.manufacturer} ${l.model}: ${LICENSE_STATUS[s]}` });
      list.reload();
      return true;
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
      return false;
    }
  }

  return (
    <div className="stack">
      <h1>Подтверждение квалификации</h1>
      <p className="muted">Заявки операторов на допуск к управлению типами БПЛА. Без подтверждённой лицензии оператор не видит и не
        может принять заказы на дроны этого типа. Статус можно изменить в любой момент — прямо в списке.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card>
        <div className="row wrap filters">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск: оператор, модель, номер, кем выдана"
                 aria-label="Поиск заявок" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Статус">
            <option value="">Все статусы</option>
            {Object.entries(LICENSE_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : sorted.length === 0 ? <Empty>Заявок нет</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{th("short_name", "Фамилия Имя")}{th("model_name", "Модель БПЛА")}{th("license_number", "Номер лицензии")}
                {th("issued_by", "Кем выдана")}{th("issued_on", "Когда получена")}{th("status_rank", "Лицензия")}</tr></thead>
              <tbody>{sorted.map((l) => (
                <tr key={l.id}>
                  <td><button className="link" onClick={() => setOpen(l)}>{l.short_name}</button></td>
                  <td>{l.model_name}</td><td>{l.license_number}</td><td>{l.issued_by}</td><td>{fmtDay(l.issued_on)}</td>
                  <td>
                    <select className={`lic-select lic-${l.status}`} value={l.status} aria-label={`Статус лицензии ${l.license_number}`}
                            onChange={(e) => {
                              const s = e.target.value;
                              const comment = s === "fix" ? prompt("Что исправить?") ?? "" : "";
                              if (s === "fix" && comment.trim().length < 3) return;
                              void decide(l, s, comment);
                            }}>
                      {l.status === "pending" && <option value="pending" disabled>На согласовании</option>}
                      <option value="approved">Подтверждена</option>
                      <option value="fix">Исправить</option>
                      <option value="rejected">Отклонена/Аннулирована</option>
                    </select>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <ReviewCard l={open} onClose={() => setOpen(null)}
                           onDecide={async (s, c) => { if (await decide(open, s, c)) setOpen(null); }} />}
    </div>
  );
}

function ReviewCard({ l, onClose, onDecide }: { l: License; onClose: () => void; onDecide: (s: string, comment: string) => Promise<void> }) {
  const [comment, setComment] = useState(l.review_comment ?? "");
  const [busy, setBusy] = useState(false);
  const run = async (s: string) => { setBusy(true); try { await onDecide(s, comment); } finally { setBusy(false); } };
  return (
    <Modal wide title={`Квалификация: ${l.short_name} — ${l.manufacturer} ${l.model}`} onClose={onClose} footer={<>
      <button type="button" className="btn" onClick={onClose}>Закрыть</button>
      <span className="spacer" />
      <button type="button" className="btn danger-fill" disabled={busy} onClick={() => void run("rejected")}>Отклонить</button>
      <button type="button" className="btn warn-fill" disabled={busy || comment.trim().length < 3} title="Напишите, что исправить"
              onClick={() => void run("fix")}>Исправить</button>
      <button type="button" className="btn ok-fill" disabled={busy} onClick={() => void run("approved")}>Согласовать</button>
    </>}>
      <LicenseCardBody l={l} extra={
        <label className="license-comment">Комментарий оператору (для «Исправить» — обязательно)
          <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} /></label>} />
    </Modal>
  );
}
