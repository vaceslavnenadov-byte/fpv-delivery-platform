import { useState } from "react";
import { api } from "../../api";
import { Badge, Card, Empty, Pending } from "../../components/ui";
import { HEALTH_STATUS } from "../../labels";
import { useLive } from "../../realtime";
import type { Drone } from "../../types";
import { DroneService } from "../../components/DroneService";


export function TechnicianPage() {
  const [filter, setFilter] = useState("");
  const list = useLive(() => api.get<{ items: Drone[] }>(`/api/v1/drones?scope=all${filter ? `&health_status=${filter}` : ""}`),
    [filter], (m) => m.kind.startsWith("drone."));
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="stack">
      <h1>Обслуживание дронов</h1>
      <Card title="Зарегистрированные дроны" actions={
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Все</option>
          {Object.entries(HEALTH_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>}>
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Нет дронов</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Учётный номер</th><th>Модель</th><th>Регистрация</th><th>Техсостояние</th><th>Связь</th><th>Батарея</th><th>ПО</th></tr></thead>
              <tbody>{list.data.items.map((d) => (
                <tr key={d.id} className={selected === d.id ? "selected" : ""} onClick={() => setSelected(d.id)}>
                  <td><button className="link" onClick={() => setSelected(d.id)}>{d.registration_number}</button></td>
                  <td>{d.manufacturer} {d.model}</td>
                  <td><Badge kind="drone" value={d.status} /></td>
                  <td><Badge kind="health" value={d.health_status} /></td>
                  <td><Badge kind="link" value={d.link_status} /></td>
                  <td className="num">{d.battery_pct != null ? `${d.battery_pct}%` : "—"}</td>
                  <td className="small">{d.firmware_version ?? "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {selected && <DroneService id={selected} />}
    </div>
  );
}

