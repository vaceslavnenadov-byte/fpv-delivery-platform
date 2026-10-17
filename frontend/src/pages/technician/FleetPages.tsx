import { RepeatBadges, RepeatBanner } from "../../components/RepeatBadges";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, errorText } from "../../api";
import { DroneConnection } from "../../components/DroneConnection";
import { OrderTable } from "../../components/OrderTable";
import { PointPicker } from "../../components/PointPicker";
import { RouteMap } from "../../components/RouteMap";
import { Alert, Badge, Card, Empty, KV, Pending, Tone } from "../../components/ui";
import { CARGO_TYPES, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import type { Zone } from "../../serviceArea";
import type { Drone, Fleet, Order } from "../../types";

// --- Мой парк: парки, дроны, ключи, открытие для заказов, подключение ---

export function FleetPage() {
  const fleets = useLive(() => api.get<{ items: Fleet[] }>("/api/v1/fleets"), []);
  const zones = useLive(() => api.get<{ items: (Zone & { open_to_all?: boolean })[] }>("/api/v1/service-zones"), [],
    (m) => m.kind === "platform.zones_changed");
  const [active, setActive] = useState<string>("");
  if (fleets.error) return <Alert>{errorText(fleets.error)}</Alert>;
  if (!fleets.data) return <Pending />;
  const list = fleets.data.items;
  const current = list.find((f) => f.id === active) ?? list[0];
  return (
    <div className="stack">
      <h1>Мой парк</h1>
      <p className="muted">Вы заводите дроны в свой парк, проверяете их состояние и подключение (телеметрия и видео),
        открываете для заказов и подтверждаете готовность дрона к каждому заказу. База парка — точка А маршрута.</p>
      {zones.data && (zones.data.items.length === 0
        ? <Alert tone="warn">Вам не назначена ни одна зона обслуживания — создать парк или перенести базу нельзя.
            Обратитесь к администратору («Зоны обслуживания»).</Alert>
        : <p className="small">Ваши зоны обслуживания (база парка и точки заказов — внутри зоны, на карте — пунктирная рамка):{" "}
            {zones.data.items.map((z) => <span key={z.id} className="chip">{z.name}{z.open_to_all ? " · общая" : ""}</span>)}</p>)}
      {list.length > 1 && (
        <div className="row">
          {list.map((f) => (
            <button key={f.id} className={`btn ${current?.id === f.id ? "primary" : ""}`} onClick={() => setActive(f.id)}>{f.name}</button>
          ))}
        </div>
      )}
      {current ? <FleetDrones key={current.id} fleet={current} onChanged={() => fleets.reload()} /> : <Empty>У вас ещё нет парка — создайте его ниже.</Empty>}
      <FleetForm onCreated={(f) => { fleets.reload(); setActive(f.id); }} first={list.length === 0} />
    </div>
  );
}

function FleetForm({ onCreated, first }: { onCreated: (f: Fleet) => void; first: boolean }) {
  const [f, setF] = useState({ name: "", address: "", lat: 55.745, lon: 37.59 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const fleet = await api.post<Fleet>("/api/v1/fleets", {
        name: f.name, base: { lat: Number(f.lat), lon: Number(f.lon), address: f.address } });
      setF({ ...f, name: "", address: "" });
      onCreated(fleet);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title={first ? "Создать парк" : "Новый парк"}>
      <form className="stack" onSubmit={submit}>
        <PointPicker value={{ lat: f.lat, lon: f.lon }} onChange={(p) => setF({ ...f, lat: p.lat, lon: p.lon })} />
        <div className="form grid4">
          <label>Название<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></label>
          <label className="span2">Адрес базы (точка А)<input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} required minLength={3} /></label>
          <span />
          <label>Широта базы<input type="number" step="0.000001" value={f.lat} onChange={(e) => setF({ ...f, lat: Number(e.target.value) })} /></label>
          <label>Долгота базы<input type="number" step="0.000001" value={f.lon} onChange={(e) => setF({ ...f, lon: Number(e.target.value) })} /></label>
          <div className="row"><button className="btn primary" disabled={busy}>Создать парк</button></div>
        </div>
      </form>
      <Alert>{error}</Alert>
    </Card>
  );
}

/** Перенос базы парка (точка А) — щелчком по карте или координатами. Действует для новых заказов. */
function BaseEditor({ fleet, onSaved }: { fleet: Fleet; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [p, setP] = useState({ lat: fleet.base.lat, lon: fleet.base.lon, address: fleet.base.address ?? "" });
  const [error, setError] = useState("");
  async function save() {
    setError("");
    try {
      await api.patch(`/api/v1/fleets/${fleet.id}`, { base: p });
      setOpen(false);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }
  if (!open) {
    return <div className="row"><button className="btn small" onClick={() => {
      setP({ lat: fleet.base.lat, lon: fleet.base.lon, address: fleet.base.address ?? "" });
      setOpen(true);
    }}>📍 Изменить базу (точку А)</button></div>;
  }
  return (
    <div className="stack base-editor">
      <PointPicker value={p} onChange={(v) => setP({ ...p, lat: v.lat, lon: v.lon })} />
      <div className="form grid4">
        <label className="span2">Адрес базы<input value={p.address} onChange={(e) => setP({ ...p, address: e.target.value })} minLength={3} /></label>
        <label>Широта<input type="number" step="0.000001" value={p.lat} onChange={(e) => setP({ ...p, lat: Number(e.target.value) })} /></label>
        <label>Долгота<input type="number" step="0.000001" value={p.lon} onChange={(e) => setP({ ...p, lon: Number(e.target.value) })} /></label>
      </div>
      <div className="row">
        <button className="btn primary small" onClick={save}>Сохранить базу</button>
        <button className="btn ghost small" onClick={() => setOpen(false)}>Отмена</button>
        <span className="small muted">Новая база действует для новых заказов; у оформленных маршрут не меняется.</span>
      </div>
      <Alert>{error}</Alert>
    </div>
  );
}

function FleetDrones({ fleet, onChanged }: { fleet: Fleet; onChanged: () => void }) {
  const data = useLive(() => api.get<{ fleet: Fleet; items: Drone[] }>(`/api/v1/fleets/${fleet.id}/drones`), [fleet.id],
    (m) => m.kind === "drone.updated");
  const [selected, setSelected] = useState<string | null>(null);
  const [key, setKey] = useState<{ drone: string; value: string } | null>(null);
  const [error, setError] = useState("");

  async function act(fn: () => Promise<unknown>) {
    setError("");
    try {
      await fn();
      data.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const items = data.data?.items ?? [];
  return (
    <>
      <Card title={`Парк «${fleet.name}»`} actions={<span className="small muted">База (А): {fleet.base.address}</span>}>
        <BaseEditor fleet={fleet} onSaved={onChanged} />
        {!data.data ? <Pending /> : items.length === 0 ? <Empty>В парке нет дронов</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Учётный номер</th><th>Модель</th><th>Регистрация</th><th>Техсостояние</th><th>Связь</th>
                <th>Видео</th><th>Для заказов</th><th></th></tr></thead>
              <tbody>{items.map((d) => (
                <tr key={d.id} className={selected === d.id ? "selected" : ""}>
                  <td><button className="link" onClick={() => setSelected(d.id)}>{d.registration_number}</button></td>
                  <td>{d.manufacturer} {d.model}<div className="small muted">до {d.max_payload_g} г · {(d.max_range_m / 1000).toFixed(0)} км</div></td>
                  <td><Badge kind="drone" value={d.status} /></td>
                  <td><Badge kind="health" value={d.health_status} /></td>
                  <td><Badge kind="link" value={d.link_status} /></td>
                  <td className="small">{d.video_checked_at ? `проверено ${fmtDate(d.video_checked_at)}` : "не проверено"}</td>
                  <td>{d.published ? <Tone tone="ok">открыт</Tone> : <Tone tone="muted">закрыт</Tone>}</td>
                  <td className="row">
                    <button className="btn small" onClick={() => setSelected(d.id)}>Подключение</button>
                    {d.status === "active" && (
                      <button className="btn small" onClick={() => act(async () => {
                        if (d.has_key && !confirm("Выпустить новый ключ? Старый перестанет действовать.")) return;
                        const r = await api.post<{ api_key: string }>(`/api/v1/drones/${d.id}/key`);
                        setKey({ drone: d.registration_number, value: r.api_key });
                      })}>{d.has_key ? "Новый ключ" : "Выпустить ключ"}</button>
                    )}
                    <button className="btn small" onClick={() => act(() => api.post(`/api/v1/drones/${d.id}/publish`, { published: !d.published }))}>
                      {d.published ? "Закрыть" : "Открыть для заказов"}
                    </button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {key && (
          <Alert tone="ok">Ключ сервисного модуля дрона {key.drone} (показывается один раз): <code>{key.value}</code>
            <div className="small">Запишите его на компьютере дрона в /etc/fpv-agent/key (права 600) и перезапустите fpv-agent.</div></Alert>
        )}
        <Alert>{error}</Alert>
        <p className="muted small">Регистрацию нового дрона подтверждает администратор; затем — осмотр в разделе «Обслуживание»,
          ключ, проверка подключения и видео, «Открыть для заказов».</p>
      </Card>
      {selected && (
        <div className="stack">
          <h2>Подключение: {items.find((d) => d.id === selected)?.registration_number}</h2>
          <DroneConnection key={selected} droneId={selected} />
        </div>
      )}
      <RegisterDrone fleetId={fleet.id} onDone={() => data.reload()} />
    </>
  );
}

function RegisterDrone({ fleetId, onDone }: { fleetId: string; onDone: () => void }) {
  const empty = { serial_number: "", registration_number: "", manufacturer: "", model: "", max_payload_g: 1500, max_range_m: 20000 };
  const [f, setF] = useState(empty);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.post("/api/v1/drones", { ...f, fleet_id: fleetId });
      setF(empty);
      setMsg({ tone: "ok", text: "Дрон заведён в парк. Ожидается подтверждение регистрации администратором." });
      onDone();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF({ ...f, [k]: typeof f[k] === "number" ? Number(e.target.value) : e.target.value });
  return (
    <Card title="Завести дрон в парк">
      <form className="form grid4" onSubmit={submit}>
        <label>Серийный номер<input value={f.serial_number} onChange={set("serial_number")} required placeholder="SIM-0002" /></label>
        <label>Учётный номер<input value={f.registration_number} onChange={set("registration_number")} required placeholder="RU-SIM-2" /></label>
        <label>Производитель<input value={f.manufacturer} onChange={set("manufacturer")} required minLength={2} /></label>
        <label>Модель<input value={f.model} onChange={set("model")} required /></label>
        <label>Грузоподъёмность, г<input type="number" min={50} value={f.max_payload_g} onChange={set("max_payload_g")} /></label>
        <label>Дальность, м<input type="number" min={500} value={f.max_range_m} onChange={set("max_range_m")} /></label>
        <div className="row"><button className="btn primary">Завести дрон</button></div>
      </form>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
    </Card>
  );
}

// --- Заказы на дронах парка ---

export function FleetOrdersPage() {
  const orders = useLive(() => api.get<{ items: Order[] }>("/api/v1/fleet-orders"), [],
    (m) => m.kind.startsWith("order."));
  return (
    <div className="stack">
      <h1>Заказы на дронах парка</h1>
      <p className="muted">По каждому заказу проверьте подключение дрона и видео и подтвердите первичную готовность к полёту —
        без этого оператор не сможет начать задание.</p>
      <Card>{!orders.data ? <Pending /> : <OrderTable orders={orders.data.items} />}</Card>
    </div>
  );
}

export function TechOrderPage() {
  const { id = "" } = useParams();
  const order = useLive(() => api.get<Order>(`/api/v1/orders/${id}`), [id],
    (m) => m.data.order_id === id);
  const [result, setResult] = useState<{ ready: boolean; reasons: string[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const o = order.data;
  if (order.error) return <Alert>{errorText(order.error)}</Alert>;
  if (!o) return <Pending />;
  const prep = ["awaiting_operator", "accepted", "awaiting_readiness", "ready"].includes(o.status);

  async function confirmReady() {
    setBusy(true);
    setError("");
    try {
      const r = await api.post<{ ready: boolean; reasons: string[] }>(`/api/v1/orders/${id}/tech-ready`);
      setResult(r);
      order.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  async function revoke() {
    const reason = prompt("Причина отзыва подтверждения готовности:");
    if (!reason) return;
    try {
      await api.post(`/api/v1/orders/${id}/tech-ready/revoke`, { reason });
      setResult(null);
      order.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="stack">
      <div className="row between">
        <RepeatBanner o={o} />
        <h1>Заказ №{o.number} <Badge kind="order" value={o.status} /> <RepeatBadges o={o} /></h1>
        <Link to="/orders" className="btn ghost">← Заказы парка</Link>
      </div>
      <div className="grid2">
        <Card title="Маршрут">
          {o.route && <RouteMap route={o.route} height={240} providerId={o.map_provider_id} orderId={o.id} />}
        </Card>
        <Card title="Заказ и готовность">
          <KV items={[
            ["Дрон", o.drone ? `${o.drone.registration_number} — ${o.drone.model}` : "—"],
            ["Груз", `${CARGO_TYPES[o.cargo_type] ?? o.cargo_type}, ${o.cargo_weight_g} г`],
            ["А — база", o.route?.base?.address ?? "—"],
            ["Б — забор груза", o.route?.origin.address],
            ...((o.route?.stops ?? []).map((x, i) => [`${i + 1} — промежуточная точка`, x.note ? `${x.address} — ${x.note}` : x.address]) as [string, string][]),
            ["В — получатель", o.route?.destination.address],
            ["Окно доставки", `${fmtDate(o.window_start)} — ${fmtDate(o.window_end)}`],
            ["Оператор", o.operator?.display_name ?? "не назначен"],
            ["Готовность (техник)", o.tech_ready_at
              ? <Tone tone="ok">подтверждена {fmtDate(o.tech_ready_at)}{o.tech_ready_by ? ` · ${o.tech_ready_by.display_name}` : ""}</Tone>
              : <Tone tone="warn">не подтверждена</Tone>],
          ]} />
          {prep && (
            <div className="row">
              <button className="btn primary" disabled={busy} onClick={confirmReady}>
                ✓ Подтвердить готовность дрона к полёту
              </button>
              {o.tech_ready_at && <button className="btn ghost" onClick={revoke}>Отозвать</button>}
            </div>
          )}
          {result && !result.ready && (
            <Alert tone="warn">Готовность не подтверждена:
              <ul className="checks">{result.reasons.map((r) => <li key={r} className="fail">✕ {r}</li>)}</ul></Alert>
          )}
          <Alert>{error}</Alert>
          <p className="muted small">Проверяется: регистрация и исправность дрона, свежая диагностика, связь, телеметрия,
            заряд, грузоподъёмность и дальность (А→Б→В и возврат на базу), видео проверено за последние 12 ч.</p>
        </Card>
      </div>
      {o.drone && <><h2>Подключение дрона</h2><DroneConnection droneId={o.drone.id} /></>}
    </div>
  );
}
