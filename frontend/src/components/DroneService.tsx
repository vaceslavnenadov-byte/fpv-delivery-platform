import { useState, type FormEvent } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Card, Empty, KV, Pending } from "./ui";
import { HEALTH_STATUS, fmtDate } from "../labels";
import { useLive } from "../realtime";
import type { Drone } from "../types";

const MAINT_KIND: Record<string, string> = {
  inspection: "Осмотр", repair: "Ремонт", component_replacement: "Замена компонентов", firmware_update: "Обновление ПО",
};

export function DroneService({ id }: { id: string }) {
  const { can } = useAuth();
  const d = useLive(() => api.get<Drone>(`/api/v1/drones/${id}`), [id], (m) => m.data?.drone_id === id);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [insp, setInsp] = useState({ result: "pass", set_status: "serviceable", battery_pct: "", error_codes: "", comment: "" });
  const [mnt, setMnt] = useState({ kind: "repair", description: "", component: "", set_status: "" });
  if (!d.data) return <Pending />;
  const drone = d.data;

  async function inspect(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/api/v1/drones/${id}/inspections`, {
        result: insp.result, set_status: insp.set_status, comment: insp.comment,
        battery_pct: insp.battery_pct ? Number(insp.battery_pct) : null,
        error_codes: insp.error_codes.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setMsg({ tone: "ok", text: "Результат осмотра зафиксирован" });
      d.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  async function maintain(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/api/v1/drones/${id}/maintenance`, {
        kind: mnt.kind, description: mnt.description,
        components: mnt.component ? [{ name: mnt.component }] : [],
        set_status: mnt.set_status || null,
      });
      setMsg({ tone: "ok", text: "Запись внесена в журнал обслуживания" });
      setMnt({ ...mnt, description: "", component: "" });
      d.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  return (
    <div className="stack">
      <h2>Дрон {drone.registration_number}</h2>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <div className="grid2">
        <Card title="Состояние">
          <KV items={[
            ["Серийный номер", drone.serial_number],
            ["Модель", `${drone.manufacturer} ${drone.model}`],
            ["Регистрация", <Badge kind="drone" value={drone.status} />],
            ["Техсостояние", <Badge kind="health" value={drone.health_status} />],
            ["Связь", <><Badge kind="link" value={drone.link_status} /> {fmtDate(drone.last_seen_at)}</>],
            ["Допуск к заданию", drone.readiness?.ready ? "Готов" : (drone.readiness?.reasons ?? []).join("; ")],
          ]} />
        </Card>
        {can("drone.health.write") && (
          <Card title="Технический осмотр">
            <form className="form grid2" onSubmit={inspect}>
              <label>Результат
                <select value={insp.result} onChange={(e) => setInsp({ ...insp, result: e.target.value })}>
                  <option value="pass">Пройден</option><option value="warn">С замечаниями</option><option value="fail">Не пройден</option>
                </select>
              </label>
              <label>Установить статус
                <select value={insp.set_status} onChange={(e) => setInsp({ ...insp, set_status: e.target.value })}>
                  {Object.entries(HEALTH_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
              </label>
              <label>Батарея, %<input type="number" min={0} max={100} value={insp.battery_pct} onChange={(e) => setInsp({ ...insp, battery_pct: e.target.value })} /></label>
              <label>Коды ошибок (через запятую)<input value={insp.error_codes} onChange={(e) => setInsp({ ...insp, error_codes: e.target.value })} /></label>
              <label className="span2">Комментарий<textarea value={insp.comment} onChange={(e) => setInsp({ ...insp, comment: e.target.value })} /></label>
              <div><button className="btn primary">Зафиксировать осмотр</button></div>
            </form>
          </Card>
        )}
      </div>
      <div className="grid2">
        <Card title="Диагностика и ошибки">
          {(drone.health_checks ?? []).length === 0 ? <Empty>Нет данных</Empty> : (
            <table className="table compact"><thead><tr><th>Дата</th><th>Источник</th><th>Результат</th><th>Ошибки</th></tr></thead>
              <tbody>{drone.health_checks!.map((c) => (
                <tr key={c.id}><td className="small">{fmtDate(c.created_at)}</td><td>{c.source === "technician" ? "Техник" : c.source === "device" ? "Устройство" : "Система"}</td>
                  <td><span className={`badge tone-${c.result === "pass" ? "ok" : c.result === "warn" ? "warn" : "danger"}`}>{c.result}</span></td>
                  <td className="small">{c.error_codes.join(", ") || "—"}</td></tr>
              ))}</tbody></table>
          )}
        </Card>
        <Card title="Журнал обслуживания">
          {can("maintenance.write") && (
            <form className="form grid2" onSubmit={maintain}>
              <label>Вид работ
                <select value={mnt.kind} onChange={(e) => setMnt({ ...mnt, kind: e.target.value })}>
                  {Object.entries(MAINT_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
              <label>Заменённый компонент<input value={mnt.component} onChange={(e) => setMnt({ ...mnt, component: e.target.value })} /></label>
              <label className="span2">Описание работ<textarea value={mnt.description} onChange={(e) => setMnt({ ...mnt, description: e.target.value })} required minLength={3} /></label>
              <label>Статус после работ
                <select value={mnt.set_status} onChange={(e) => setMnt({ ...mnt, set_status: e.target.value })}>
                  <option value="">Не менять</option><option value="requires_check">Требует проверки</option><option value="grounded">Не допущен к эксплуатации</option>
                </select>
              </label>
              <div><button className="btn">Добавить запись</button></div>
            </form>
          )}
          <ul className="list">
            {(drone.maintenance ?? []).map((m) => (
              <li key={m.id}><strong>{MAINT_KIND[m.kind] ?? m.kind}</strong> · {fmtDate(m.created_at)}
                <div className="small">{m.description}{m.components.length > 0 && ` — ${m.components.map((c) => c.name).join(", ")}`}</div></li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
