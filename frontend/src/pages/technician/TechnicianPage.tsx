import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../../api";
import { Badge, Card, Empty, Pending, Tone, useSort } from "../../components/ui";
import { DRONE_STATUS, HEALTH_STATUS, LINK_STATUS } from "../../labels";
import { useLive } from "../../realtime";
import type { Drone } from "../../types";
import { DroneService } from "../../components/DroneService";


export function TechnicianPage() {
  const [filter, setFilter] = useState("");
  const list = useLive(() => api.get<{ items: Drone[] }>(`/api/v1/drones?scope=all${filter ? `&health_status=${filter}` : ""}`),
    [filter], (m) => m.kind.startsWith("drone."));
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("drone"));
  const rows = (list.data?.items ?? []).map((d) => ({ ...d, model_name: `${d.manufacturer} ${d.model}`,
    reg_label: DRONE_STATUS[d.status]?.label ?? d.status, health_label: HEALTH_STATUS[d.health_status]?.label ?? d.health_status,
    link_label: LINK_STATUS[d.link_status]?.label ?? d.link_status, battery: d.battery_pct ?? -1, fw: d.firmware_version ?? "" }));
  const { sorted, th } = useSort(rows, "registration_number");
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
              <thead><tr>{th("registration_number", "Учётный номер")}{th("model_name", "Модель")}{th("reg_label", "Регистрация")}
                {th("health_label", "Техсостояние")}{th("link_label", "Связь")}{th("battery", "Батарея", true)}{th("fw", "ПО")}</tr></thead>
              <tbody>{sorted.map((d) => (
                <tr key={d.id} className={selected === d.id ? "selected" : ""} onClick={() => setSelected(d.id)}>
                  <td><button className="link" onClick={() => setSelected(d.id)}>{d.registration_number}</button></td>
                  <td>{d.manufacturer} {d.model} {d.archived && <Tone tone="muted">в архиве</Tone>}</td>
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

