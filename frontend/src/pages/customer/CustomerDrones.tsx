import { useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Alert, Badge, Card, Empty, Pending } from "../../components/ui";
import { fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import type { Drone } from "../../types";

export function CustomerDronesPage() {
  const drones = useLive(() => api.get<{ items: Drone[] }>("/api/v1/drones"), [], (m) => m.kind.startsWith("drone."));
  const [form, setForm] = useState({ serial_number: "", registration_number: "", manufacturer: "", model: "",
    max_payload_g: 1000, max_range_m: 15000 });
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [key, setKey] = useState<{ drone: string; key: string } | null>(null);

  async function register(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/v1/drones", form);
      setMsg({ tone: "ok", text: "Дрон зарегистрирован и ожидает подтверждения администратором и осмотра техником" });
      drones.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  async function issueKey(d: Drone) {
    if (d.has_key && !confirm("Выпустить новый ключ? Старый ключ перестанет действовать.")) return;
    try {
      const r = await api.post<{ api_key: string }>(`/api/v1/drones/${d.id}/key`);
      setKey({ drone: d.registration_number, key: r.api_key });
      drones.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  const set = (k: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: k.startsWith("max_") ? Number(e.target.value) : e.target.value }));

  return (
    <div className="stack">
      <h1>Мои дроны</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {key && (
        <Alert tone="warn">
          Ключ подключения сервисного модуля дрона {key.drone} (показывается один раз, сохраните его в файл с правами 600):
          <pre className="secret">{key.key}</pre>
          <button className="btn small" onClick={() => setKey(null)}>Я сохранил ключ</button>
        </Alert>
      )}
      <Card title="Зарегистрированные дроны">
        {!drones.data ? <Pending /> : drones.data.items.length === 0 ? <Empty>Дронов пока нет</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Учётный номер</th><th>Модель</th><th>Регистрация</th><th>Техсостояние</th><th>Связь</th><th>Батарея</th><th>Ключ</th><th /></tr></thead>
              <tbody>
                {drones.data.items.map((d) => (
                  <tr key={d.id}>
                    <td><strong>{d.registration_number}</strong><br /><span className="muted small">S/N {d.serial_number}</span></td>
                    <td>{d.manufacturer} {d.model}<br /><span className="muted small">до {d.max_payload_g} г, {d.max_range_m / 1000} км</span></td>
                    <td><Badge kind="drone" value={d.status} /></td>
                    <td><Badge kind="health" value={d.health_status} /></td>
                    <td><Badge kind="link" value={d.link_status} /><br /><span className="muted small">{fmtDate(d.last_seen_at)}</span></td>
                    <td className="num">{d.battery_pct != null ? `${d.battery_pct}%` : "—"}</td>
                    <td className="small">{d.has_key ? `${d.key_prefix}…` : "нет"}</td>
                    <td>{d.status === "active" && <button className="btn small" onClick={() => issueKey(d)}>{d.has_key ? "Перевыпустить ключ" : "Выпустить ключ"}</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Регистрация дрона">
        <form className="form grid3" onSubmit={register}>
          <label>Серийный номер<input value={form.serial_number} onChange={set("serial_number")} required /></label>
          <label>Учётный (регистрационный) номер<input value={form.registration_number} onChange={set("registration_number")} required /></label>
          <label>Производитель<input value={form.manufacturer} onChange={set("manufacturer")} required /></label>
          <label>Модель<input value={form.model} onChange={set("model")} required /></label>
          <label>Грузоподъёмность, г<input type="number" min={50} value={form.max_payload_g} onChange={set("max_payload_g")} /></label>
          <label>Дальность, м<input type="number" min={500} value={form.max_range_m} onChange={set("max_range_m")} /></label>
          <div><button className="btn primary">Зарегистрировать</button></div>
        </form>
        <p className="muted small">Дрон допускается к заказам после подтверждения регистрации администратором и технического осмотра.</p>
      </Card>
    </div>
  );
}
