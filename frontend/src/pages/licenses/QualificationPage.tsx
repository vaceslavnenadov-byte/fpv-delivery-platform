import { useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Alert, Card, Empty, Modal, Pending, useSort } from "../../components/ui";
import { loadDroneModels, type DroneModel } from "../../catalogs";
import { useLive } from "../../realtime";
import { LicenseCardBody, LicenseStatus, fmtDay, type License } from "./common";

// Оператор: «Квалификация» — типы БПЛА, к управлению которыми он допущен, и заявки на допуск.
// Заказы на дрон типа из справочника видит только оператор с подтверждённой лицензией на этот тип.

function query(f: Record<string, string>): string {
  return new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "")).toString();
}

export function QualificationPage() {
  const list = useLive(() => api.get<{ items: License[] }>("/api/v1/me/licenses"), [],
    (m) => m.kind === "operator.license" || m.kind === "notification.created");
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<License | null>(null);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const rows = (list.data?.items ?? []).map((l) => ({ ...l, status_rank: { approved: 0, pending: 1, fix: 2, rejected: 3 }[l.status] }));
  const { sorted, th } = useSort(rows, "model");
  return (
    <div className="stack">
      <div className="row between wrap">
        <h1>Квалификация</h1>
        <button className="btn ok-fill" onClick={() => setCreating(true)}>+ Загрузить новую лицензию</button>
      </div>
      <p className="muted">Типы БПЛА, к управлению которыми вы допущены. Заказы на дроны этих типов вы увидите в очереди только
        после подтверждения лицензии администратором или рекрутёром. На дроны, заведённые без типа из справочника, допуск не нужен.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : sorted.length === 0
          ? <Empty>Лицензий пока нет — нажмите «Загрузить новую лицензию»</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{th("model", "Модель")}{th("manufacturer", "Производитель")}{th("link_type", "Тип связи")}
                {th("construction", "Конструкция")}{th("engine_type", "Тип двигателя")}{th("license_number", "Номер лицензии")}
                {th("issued_on", "Когда получена")}{th("issued_by", "Кем выдана")}{th("status_rank", "Лицензия")}</tr></thead>
              <tbody>{sorted.map((l) => (
                <tr key={l.id} className="clickable" onClick={() => setOpen(l)}>
                  <td><button className="link" onClick={() => setOpen(l)}>{l.model}</button></td>
                  <td>{l.manufacturer}</td><td>{l.link_type || "—"}</td><td>{l.construction || "—"}</td><td>{l.engine_type || "—"}</td>
                  <td>{l.license_number}</td><td>{fmtDay(l.issued_on)}</td><td>{l.issued_by}</td>
                  <td><LicenseStatus s={l.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {creating && <LicenseForm onClose={() => setCreating(false)}
        onDone={() => { setCreating(false); setMsg({ tone: "ok", text: "Лицензия отправлена на подтверждение" }); list.reload(); }} />}
      {open && <MyLicenseCard l={open} onClose={() => setOpen(null)}
        onDone={() => { setOpen(null); setMsg({ tone: "ok", text: "Исправленная заявка отправлена на подтверждение" }); list.reload(); }} />}
    </div>
  );
}

/** Новая заявка или исправление (license задан и в статусе «Исправить»). */
function LicenseForm({ license, onClose, onDone }: { license?: License; onClose: () => void; onDone: () => void }) {
  const models = useLive(loadDroneModels, []);
  const [f, setF] = useState({ drone_model_id: license?.drone_model_id ?? "", license_number: license?.license_number ?? "",
                               issued_on: license?.issued_on ?? "", issued_by: license?.issued_by ?? "" });
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = (models.data ?? []).find((m) => m.id === f.drone_model_id);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!license && !file) { setError("Приложите скан лицензии"); return; }
    setBusy(true);
    setError("");
    try {
      const q = query({ license_number: f.license_number, issued_on: f.issued_on, issued_by: f.issued_by, filename: file?.name ?? "" });
      const body = file ?? new Blob([]);
      if (license) await api.uploadPut(`/api/v1/me/licenses/${license.id}?${q}`, body);
      else await api.upload(`/api/v1/me/licenses?${q}&drone_model_id=${encodeURIComponent(f.drone_model_id)}`, body);
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const form = (
    <form id="license-form" className="form grid2" onSubmit={submit}>
      <label className="span2">Тип БПЛА
        <select value={f.drone_model_id} onChange={(e) => setF({ ...f, drone_model_id: e.target.value })} required disabled={!!license}
                aria-label="Тип БПЛА">
          <option value="" disabled>— выберите из «Типов БПЛА» —</option>
          {(models.data ?? []).map((m: DroneModel) => <option key={m.id} value={m.id}>{m.manufacturer} {m.model}</option>)}
        </select>
      </label>
      {chosen && <p className="span2 small muted">Тип связи: {chosen.link_type || "—"} · конструкция: {chosen.construction || "—"} ·
        двигатель: {chosen.engine_type || "—"}</p>}
      <label>Номер лицензии<input value={f.license_number} onChange={(e) => setF({ ...f, license_number: e.target.value })} required maxLength={100} /></label>
      <label>Когда выдана<input type="date" value={f.issued_on} onChange={(e) => setF({ ...f, issued_on: e.target.value })} required /></label>
      <label className="span2">Кем выдана<input value={f.issued_by} onChange={(e) => setF({ ...f, issued_by: e.target.value })} required minLength={2} maxLength={300} /></label>
      <label className="span2">Скан лицензии (изображение или PDF, до 15 МБ){license && <span className="small muted"> — не выбирайте, чтобы оставить прежний</span>}
        <input type="file" accept="image/png,image/jpeg,image/webp,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
               required={!license} aria-label="Скан лицензии" /></label>
    </form>
  );
  return (
    <Modal title={license ? "Исправить заявку на квалификацию" : "Новая лицензия"} onClose={onClose} footer={<>
      <button type="button" className="btn danger-fill" onClick={onClose}>Отмена</button>
      <span className="spacer" />
      <button type="submit" form="license-form" className="btn ok-fill" disabled={busy}>{busy ? "Отправка…" : "Отправить на подтверждение"}</button>
    </>}>
      {form}
      <Alert>{error}</Alert>
    </Modal>
  );
}

function MyLicenseCard({ l, onClose, onDone }: { l: License; onClose: () => void; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <LicenseForm license={l} onClose={() => setEditing(false)} onDone={onDone} />;
  return (
    <Modal wide title={`Заявка на квалификацию: ${l.manufacturer} ${l.model}`} onClose={onClose} footer={<>
      <span className="spacer" />
      {l.status === "fix" && <button type="button" className="btn warn-fill" onClick={() => setEditing(true)}>Исправить и отправить снова</button>}
      <button type="button" className="btn" onClick={onClose}>Закрыть</button>
    </>}>
      <LicenseCardBody l={l} />
    </Modal>
  );
}
