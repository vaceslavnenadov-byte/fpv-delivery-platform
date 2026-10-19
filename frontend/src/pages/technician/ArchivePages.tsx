import { useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "../../api";
import { Alert, Card, Empty, Modal, Pending, useSort } from "../../components/ui";
import { DroneService } from "../../components/DroneService";
import { fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { archiveReasonText, downloadReport, droneStatusLabel, reportFileName, reportOrder, ruDate,
  type ReportDrone, type TechReport } from "./techReport";

// --- Архив БПЛА: дроны, перенесённые техником из «Мой парк» ---

export function DroneArchivePage() {
  const list = useLive(() => api.get<{ items: ReportDrone[] }>("/api/v1/drone-archive"), [],
    (m) => m.kind === "drone.updated");
  const rows = (list.data?.items ?? []).map((d) => ({ ...d, model_name: `${d.manufacturer} ${d.model}`, reason: archiveReasonText(d) }));
  const { sorted, th } = useSort(rows, "archived_at");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="stack">
      <h1>Архив БПЛА</h1>
      <p className="muted">Дроны, перенесённые в архив, не показываются в «Мой парк» и недоступны для заказов.
        Вернуть дрон в парк — в разделе «Обслуживание», кнопка «Переместить из архива» в «Журнале обслуживания».</p>
      <Card>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : rows.length === 0 ? <Empty>Архив пуст</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{th("registration_number", "Учётный номер")}{th("model_name", "Модель")}{th("serial_number", "Серийный номер")}
                {th("archived_at", "Дата")}{th("reason", "Причина")}<th /></tr></thead>
              <tbody>{sorted.map((d) => (
                <tr key={d.id}>
                  <td><button className="link" onClick={() => setOpen(d.id)} title="Открыть карточку БПЛА">{d.registration_number}</button></td>
                  <td>{d.model_name}<div className="small muted">парк «{d.fleet_name}»</div></td>
                  <td>{d.serial_number}</td>
                  <td className="small">{fmtDate(d.archived_at)}</td><td>{d.reason}</td>
                  <td><Link className="btn small" to={`/service?drone=${d.id}`}>Открыть в «Обслуживании»</Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {open && (
        <Modal wide title="Карточка БПЛА" onClose={() => { setOpen(null); list.reload(); }}
               footer={<button type="button" className="btn" onClick={() => { setOpen(null); list.reload(); }}>Закрыть</button>}>
          <DroneService id={open} />
        </Modal>
      )}
    </div>
  );
}

// --- Отчёт о техсостоянии (PDF) ---

export function TechReportPage() {
  const report = useLive(() => api.get<TechReport>("/api/v1/tech-report"), [], (m) => m.kind === "drone.updated");
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const r = report.data;
  async function make() {
    if (!r) return;
    setBusy(true);
    setMsg(null);
    try {
      const name = await downloadReport(r);
      setMsg({ tone: "ok", text: `Отчёт сохранён: ${name} (папка загрузок браузера — обычно /home/<пользователь>/Загрузки).` });
    } catch (err) {
      setMsg({ tone: "danger", text: `Не удалось сформировать PDF: ${errorText(err)}` });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="stack">
      <h1>Отчёт о ТехСостоянии</h1>
      <p className="muted">PDF со всеми дронами ваших парков, включая архивные: модель, учётный и серийный номера, дата
        последнего полёта, статус и причина архивации. Сначала работоспособные дроны, архивные — в конце.</p>
      {report.error ? <Alert>{errorText(report.error)}</Alert> : null}
      {!r ? <Pending /> : (
        <Card title={`Дронов: ${r.items.length}`} actions={
          <button className="btn primary" disabled={busy} onClick={make}>{busy ? "Формирование…" : "⤓ Сформировать PDF"}</button>}>
          <p className="small">Файл: <code>{reportFileName(r.technician, new Date())}</code></p>
          {r.items.length === 0 ? <Empty>В ваших парках нет дронов</Empty> : (
            <div className="table-wrap">
              <table className="table compact">
                <thead><tr><th>Модель</th><th>Учётный номер</th><th>Серийный номер</th><th>Последний полёт</th><th>Статус</th><th>Причина архивации</th></tr></thead>
                <tbody>{reportOrder(r.items).map((d) => (
                  <tr key={d.id} className={d.archived ? "muted" : ""}>
                    <td>{d.manufacturer} {d.model}</td><td>{d.registration_number}</td><td>{d.serial_number}</td>
                    <td>{d.last_flight_at ? ruDate(new Date(d.last_flight_at)) : "—"}</td>
                    <td>{droneStatusLabel(d)}</td><td>{archiveReasonText(d)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
          <p className="small muted">Браузер сохраняет файл в папку загрузок (в Debian с русским языком — /home/&lt;пользователь&gt;/Загрузки).
            Если браузер спрашивает, куда сохранить, выберите эту папку.</p>
        </Card>
      )}
    </div>
  );
}
