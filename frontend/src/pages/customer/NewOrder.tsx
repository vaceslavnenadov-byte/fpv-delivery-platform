import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, errorText, newIdempotencyKey } from "../../api";
import { MapPointsEditor } from "../../components/MapPointsEditor";
import { LOCAL } from "../../tiles";
import { RouteMap } from "../../components/RouteMap";
import { Alert, Card, Pending } from "../../components/ui";
import { CARGO_TYPES } from "../../labels";
import { useLive } from "../../realtime";
import type { AvailableDrone, Order, Stop } from "../../types";

const PRESETS = [
  { name: "Склад (ул. Примерная, 1)", lat: 55.75, lon: 37.6 },
  { name: "Офис (пр. Тестовый, 10)", lat: 55.76, lon: 37.63 },
  { name: "Поликлиника (ул. Лесная, 5)", lat: 55.78, lon: 37.58 },
  { name: "Пункт выдачи (ул. Речная, 12)", lat: 55.73, lon: 37.66 },
];

const MAX_STOPS = 5;
const mapAddress = (lat: number, lon: number) => `Точка на карте (${lat.toFixed(5)}, ${lon.toFixed(5)})`;
const isAutoAddress = (a: string) => !a.trim() || PRESETS.some((x) => x.name === a) || a.startsWith("Точка на карте");

function localInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function NewOrderPage() {
  const navigate = useNavigate();
  // Дроны, которые техники открыли для заказов (по паркам); база парка — точка А маршрута.
  const drones = useLive(() => api.get<{ items: AvailableDrone[] }>("/api/v1/available-drones"), [],
    (m) => m.kind === "drone.updated");
  const now = new Date();
  const [f, setF] = useState({
    drone_id: "", cargo_type: "documents", cargo_description: "", cargo_weight_g: 300,
    cargo_length_mm: 300, cargo_width_mm: 220, cargo_height_mm: 30,
    origin_address: PRESETS[0].name, origin_lat: PRESETS[0].lat, origin_lon: PRESETS[0].lon,
    dest_address: PRESETS[1].name, dest_lat: PRESETS[1].lat, dest_lon: PRESETS[1].lon,
    recipient_name: "", recipient_phone: "",
    window_start: localInput(new Date(now.getTime() + 60 * 60000)),
    window_end: localInput(new Date(now.getTime() + 120 * 60000)),
  });
  const [order, setOrder] = useState<Order | null>(null);
  const [activePin, setActivePin] = useState("origin");
  // Карта, на которой заказчик прокладывает маршрут; онлайн-карта сохраняется в заказе и по
  // умолчанию показывается оператору и координатору.
  const [mapSource, setMapSource] = useState("");
  // Промежуточные точки между Б и В — по порядку прохождения.
  const [stops, setStops] = useState<Stop[]>([]);
  const changeStops = (fn: (s: Stop[]) => Stop[]) => {
    idemKey.current = newIdempotencyKey();
    setStops(fn);
  };
  const addStop = () => changeStops((list) => {
    // новая точка — посередине последнего участка до В, чтобы сразу была видна на карте
    const prev = list.length ? list[list.length - 1] : { lat: f.origin_lat, lon: f.origin_lon };
    const lat = Math.round(((prev.lat + f.dest_lat) / 2) * 1e6) / 1e6;
    const lon = Math.round(((prev.lon + f.dest_lon) / 2) * 1e6) / 1e6;
    setActivePin(`stop-${list.length}`);
    return [...list, { lat, lon, address: mapAddress(lat, lon), note: "" }];
  });
  const editStop = (i: number, patch: Partial<Stop>) => changeStops((list) => list.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const moveStop = (i: number, d: number) => changeStops((list) => {
    const next = [...list];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    return next;
  });
  const removeStop = (i: number) => {
    setActivePin("dest");
    changeStops((list) => list.filter((_, j) => j !== i));
  };
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Ключ идемпотентности живёт, пока форма не изменена: повторная отправка не создаёт дубликат.
  const idemKey = useRef(newIdempotencyKey());

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    idemKey.current = newIdempotencyKey();
    const numeric = typeof f[k] === "number";
    setF((s) => ({ ...s, [k]: numeric ? Number(e.target.value) : e.target.value }));
  };

  // Перетаскивание метки Б/В: координаты берутся с карты; название из списка больше не подходит —
  // адрес заменяется на «Точка на карте», заказчик может уточнить его в поле «Адрес».
  const movePin = (key: string, p: { lat: number; lon: number }) => {
    idemKey.current = newIdempotencyKey();
    if (key.startsWith("stop-")) {
      const i = Number(key.slice(5));
      changeStops((list) => list.map((x, j) => (j === i
        ? { ...x, lat: p.lat, lon: p.lon, address: isAutoAddress(x.address) ? mapAddress(p.lat, p.lon) : x.address } : x)));
      return;
    }
    const pre = key === "origin" ? "origin" : "dest";
    setF((s) => {
      const addr = s[`${pre}_address`];
      return { ...s, [`${pre}_lat`]: p.lat, [`${pre}_lon`]: p.lon,
               [`${pre}_address`]: isAutoAddress(addr) ? mapAddress(p.lat, p.lon) : addr };
    });
  };

  const usable = (drones.data?.items ?? []).filter((d) => !d.busy);
  const chosen = usable.find((d) => d.id === f.drone_id) ?? usable[0];
  const base = chosen?.fleet.base;
  const fleets = Array.from(new Map(usable.map((d) => [d.fleet.id, d.fleet.name])).entries());

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = {
        drone_id: chosen?.id, cargo_type: f.cargo_type, cargo_description: f.cargo_description,
        cargo_weight_g: f.cargo_weight_g, cargo_length_mm: f.cargo_length_mm, cargo_width_mm: f.cargo_width_mm,
        cargo_height_mm: f.cargo_height_mm,
        origin: { lat: f.origin_lat, lon: f.origin_lon, address: f.origin_address },
        destination: { lat: f.dest_lat, lon: f.dest_lon, address: f.dest_address },
        map_provider_id: mapSource && mapSource !== LOCAL ? mapSource : null,
        stops: stops.map((x) => ({ lat: x.lat, lon: x.lon, address: x.address, note: x.note ?? "" })),
        recipient_name: f.recipient_name, recipient_phone: f.recipient_phone,
        window_start: new Date(f.window_start).toISOString(), window_end: new Date(f.window_end).toISOString(),
      };
      const draft = await api.post<Order>("/api/v1/orders", body, { idempotencyKey: idemKey.current });
      const checked = await api.post<Order>(`/api/v1/orders/${draft.id}/submit`, { expected_version: draft.version },
        { idempotencyKey: `${idemKey.current}-submit` });
      setOrder(checked);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmOrder() {
    if (!order) return;
    setBusy(true);
    try {
      const o = await api.post<Order>(`/api/v1/orders/${order.id}/confirm`, { expected_version: order.version },
        { idempotencyKey: `${idemKey.current}-confirm` });
      navigate(`/orders/${o.id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!drones.data) return <Pending />;

  if (order) {
    const checks = order.validation_result?.checks ?? [];
    const ok = order.status === "pending_confirmation";
    return (
      <div className="stack">
        <h1>Заказ №{order.number}: проверка условий</h1>
        <Alert tone={ok ? "ok" : "danger"}>
          {ok ? "Все проверки пройдены. Подтвердите условия, чтобы передать заказ операторам."
              : "Заказ не может быть подтверждён: исправьте условия и создайте заказ заново."}
        </Alert>
        <div className="grid2">
          <Card title="Результаты проверки">
            <ul className="checks">
              {checks.map((c) => <li key={c.code} className={c.ok ? "ok" : "fail"}>{c.ok ? "✓" : "✕"} {c.message}</li>)}
            </ul>
          </Card>
          <Card title="Условия выполнения">
            {order.route && <RouteMap route={order.route} height={220} providerId={order.map_provider_id} orderId={order.id} />}
            <p className="price">{order.quote_amount ? `${order.quote_amount} ₽` : "—"}</p>
            <p className="muted small">Предварительная стоимость: базовый тариф + расстояние полёта (А→Б→В) + масса груза.</p>
          </Card>
        </div>
        <Alert>{error}</Alert>
        <div className="row">
          {ok && <button className="btn primary" disabled={busy} onClick={confirmOrder}>Подтвердить заказ</button>}
          <button className="btn" onClick={() => navigate(`/orders/${order.id}`)}>Открыть карточку заказа</button>
          <button className="btn ghost" onClick={() => { setOrder(null); idemKey.current = newIdempotencyKey(); }}>Новый заказ</button>
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      <h1>Новый заказ</h1>
      {usable.length === 0 && <Alert tone="warn">Сейчас нет свободных дронов: техники парков ещё не открыли дроны для заказов или все заняты.</Alert>}
      <form className="stack" onSubmit={create}>
        <div className="grid2">
          <Card title="Груз">
            <div className="form grid2">
              <label className="span2">Дрон (из открытых парков)
                <select value={chosen?.id ?? ""} onChange={set("drone_id")} required>
                  {fleets.map(([id, name]) => (
                    <optgroup key={id} label={`Парк «${name}»`}>
                      {usable.filter((d) => d.fleet.id === id).map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.registration_number} — {d.manufacturer} {d.model}, до {d.max_payload_g} г, дальность {(d.max_range_m / 1000).toFixed(0)} км
                          {d.link_status === "online" ? "" : " (сейчас не на связи)"}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              <label>Тип груза
                <select value={f.cargo_type} onChange={set("cargo_type")}>
                  {Object.entries(CARGO_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
              <label className="span2">Описание груза<textarea value={f.cargo_description} onChange={set("cargo_description")} required minLength={3} /></label>
              <label>Масса, г<input type="number" min={1} value={f.cargo_weight_g} onChange={set("cargo_weight_g")} /></label>
              <div className="row">
                <label>Д, мм<input type="number" min={1} value={f.cargo_length_mm} onChange={set("cargo_length_mm")} /></label>
                <label>Ш, мм<input type="number" min={1} value={f.cargo_width_mm} onChange={set("cargo_width_mm")} /></label>
                <label>В, мм<input type="number" min={1} value={f.cargo_height_mm} onChange={set("cargo_height_mm")} /></label>
              </div>
            </div>
          </Card>
          <Card title="Маршрут">
            <MapPointsEditor active={activePin} source={mapSource} onSourceChange={setMapSource} onActiveChange={setActivePin} onChange={movePin} height={340}
              emptyHint="Карт района нет — выберите точки из списка или укажите координаты (карту загружает администратор)."
              pins={[
                ...(base ? [{ key: "base", label: "А", title: "А — база парка", point: base, className: "map-base-pin", locked: true }] : []),
                { key: "origin", label: "Б", title: "Б — где забрать груз", point: { lat: f.origin_lat, lon: f.origin_lon }, className: "map-pickup" },
                ...stops.map((x, i) => ({ key: `stop-${i}`, label: String(i + 1), title: `${i + 1} — промежуточная точка`,
                                          point: { lat: x.lat, lon: x.lon }, className: "map-stop" })),
                { key: "dest", label: "В", title: "В — получатель", point: { lat: f.dest_lat, lon: f.dest_lon }, className: "map-b" },
              ]} />
            <div className="form grid2">
              <div className="span2 muted small">
                <b>А — база парка:</b> {base ? `${base.address} (${base.lat.toFixed(4)}, ${base.lon.toFixed(4)})` : "—"}.
                Дрон вылетает с базы, забирает груз в точке Б, проходит промежуточные точки (если есть) и доставляет получателю в точку В.
                Базу парка меняет техник, поэтому метка А закреплена.
              </div>
              <label className="span2">Точка Б — где забрать груз
                <select onChange={(e) => { const p = PRESETS[Number(e.target.value)]; idemKey.current = newIdempotencyKey();
                  setF((s) => ({ ...s, origin_address: p.name, origin_lat: p.lat, origin_lon: p.lon })); }}
                  value={PRESETS.findIndex((p) => p.name === f.origin_address)}>
                  {PRESETS.map((p, i) => <option key={p.name} value={i}>{p.name}</option>)}
                  <option value={-1}>Другая точка (координаты ниже)</option>
                </select>
              </label>
              <label>Широта Б<input type="number" step="0.0001" value={f.origin_lat} onChange={set("origin_lat")} /></label>
              <label>Долгота Б<input type="number" step="0.0001" value={f.origin_lon} onChange={set("origin_lon")} /></label>
              <label className="span2">Адрес Б<input value={f.origin_address} onChange={set("origin_address")} /></label>
              <div className="span2 stops">
                <div className="row between">
                  <strong>Промежуточные точки</strong>
                  <button type="button" className="btn small" onClick={addStop} disabled={stops.length >= MAX_STOPS}>
                    + Добавить точку
                  </button>
                </div>
                {stops.length === 0 ? (
                  <p className="muted small">Нужно заехать по пути от Б к В? Добавьте до {MAX_STOPS} точек — они пройдутся по порядку.</p>
                ) : stops.map((x, i) => (
                  <div key={i} className="stop-row">
                    <span className="pin-dot map-stop" aria-hidden>{i + 1}</span>
                    <input value={x.address} onChange={(e) => editStop(i, { address: e.target.value })} required minLength={3}
                           aria-label={`Адрес точки ${i + 1}`} placeholder="Адрес" />
                    <input value={x.note ?? ""} onChange={(e) => editStop(i, { note: e.target.value })} maxLength={200}
                           aria-label={`Комментарий к точке ${i + 1}`} placeholder="Что сделать (необязательно)" />
                    <input type="number" step="0.0001" value={x.lat} onChange={(e) => editStop(i, { lat: Number(e.target.value) })}
                           aria-label={`Широта точки ${i + 1}`} />
                    <input type="number" step="0.0001" value={x.lon} onChange={(e) => editStop(i, { lon: Number(e.target.value) })}
                           aria-label={`Долгота точки ${i + 1}`} />
                    <span className="row">
                      <button type="button" className="btn small ghost" disabled={i === 0} onClick={() => moveStop(i, -1)}
                              aria-label={`Точку ${i + 1} раньше`} title="Раньше">↑</button>
                      <button type="button" className="btn small ghost" disabled={i === stops.length - 1} onClick={() => moveStop(i, 1)}
                              aria-label={`Точку ${i + 1} позже`} title="Позже">↓</button>
                      <button type="button" className="btn small ghost" onClick={() => removeStop(i)}
                              aria-label={`Удалить точку ${i + 1}`} title="Удалить">✕</button>
                    </span>
                  </div>
                ))}
              </div>
              <label className="span2">Точка В — куда доставить (получатель)
                <select onChange={(e) => { const p = PRESETS[Number(e.target.value)]; idemKey.current = newIdempotencyKey();
                  setF((s) => ({ ...s, dest_address: p.name, dest_lat: p.lat, dest_lon: p.lon })); }}
                  value={PRESETS.findIndex((p) => p.name === f.dest_address)}>
                  {PRESETS.map((p, i) => <option key={p.name} value={i}>{p.name}</option>)}
                  <option value={-1}>Другая точка (координаты ниже)</option>
                </select>
              </label>
              <label>Широта В<input type="number" step="0.0001" value={f.dest_lat} onChange={set("dest_lat")} /></label>
              <label>Долгота В<input type="number" step="0.0001" value={f.dest_lon} onChange={set("dest_lon")} /></label>
              <label className="span2">Адрес В<input value={f.dest_address} onChange={set("dest_address")} /></label>
            </div>
          </Card>
        </div>
        <Card title="Получатель и временное окно">
          <div className="form grid4">
            <label>Получатель<input value={f.recipient_name} onChange={set("recipient_name")} required minLength={2} /></label>
            <label>Телефон получателя<input value={f.recipient_phone} onChange={set("recipient_phone")} required placeholder="+7 900 000-00-00" /></label>
            <label>Окно: с<input type="datetime-local" value={f.window_start} onChange={set("window_start")} required /></label>
            <label>Окно: до<input type="datetime-local" value={f.window_end} onChange={set("window_end")} required /></label>
          </div>
        </Card>
        <Alert>{error}</Alert>
        <div><button className="btn primary" disabled={busy || usable.length === 0}>{busy ? "Проверка…" : "Проверить и рассчитать стоимость"}</button></div>
      </form>
    </div>
  );
}
