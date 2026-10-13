import { useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, errorText, newIdempotencyKey } from "../../api";
import { RouteMap } from "../../components/RouteMap";
import { Alert, Card, Pending } from "../../components/ui";
import { CARGO_TYPES } from "../../labels";
import { useLive } from "../../realtime";
import type { Drone, Order } from "../../types";

const PRESETS = [
  { name: "Склад (ул. Примерная, 1)", lat: 55.75, lon: 37.6 },
  { name: "Офис (пр. Тестовый, 10)", lat: 55.76, lon: 37.63 },
  { name: "Поликлиника (ул. Лесная, 5)", lat: 55.78, lon: 37.58 },
  { name: "Пункт выдачи (ул. Речная, 12)", lat: 55.73, lon: 37.66 },
];

function localInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function NewOrderPage() {
  const navigate = useNavigate();
  const drones = useLive(() => api.get<{ items: Drone[] }>("/api/v1/drones"), []);
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Ключ идемпотентности живёт, пока форма не изменена: повторная отправка не создаёт дубликат.
  const idemKey = useRef(newIdempotencyKey());

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    idemKey.current = newIdempotencyKey();
    const numeric = typeof f[k] === "number";
    setF((s) => ({ ...s, [k]: numeric ? Number(e.target.value) : e.target.value }));
  };

  const usable = (drones.data?.items ?? []).filter((d) => d.status === "active");
  const preview = useMemo(() => ({
    origin: { lat: f.origin_lat, lon: f.origin_lon }, destination: { lat: f.dest_lat, lon: f.dest_lon },
    distance_m: 0, max_altitude_m: 120, checkpoints: [], approval_status: "pending", approval_notes: [],
  }), [f.origin_lat, f.origin_lon, f.dest_lat, f.dest_lon]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = {
        drone_id: f.drone_id || usable[0]?.id, cargo_type: f.cargo_type, cargo_description: f.cargo_description,
        cargo_weight_g: f.cargo_weight_g, cargo_length_mm: f.cargo_length_mm, cargo_width_mm: f.cargo_width_mm,
        cargo_height_mm: f.cargo_height_mm,
        origin: { lat: f.origin_lat, lon: f.origin_lon, address: f.origin_address },
        destination: { lat: f.dest_lat, lon: f.dest_lon, address: f.dest_address },
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
            {order.route && <RouteMap route={order.route} height={220} />}
            <p className="price">{order.quote_amount ? `${order.quote_amount} ₽` : "—"}</p>
            <p className="muted small">Предварительная стоимость: базовый тариф + расстояние + масса груза.</p>
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
      {usable.length === 0 && <Alert tone="warn">Нет дронов с подтверждённой регистрацией. Зарегистрируйте дрон в разделе «Мои дроны».</Alert>}
      <form className="stack" onSubmit={create}>
        <div className="grid2">
          <Card title="Груз">
            <div className="form grid2">
              <label>Дрон
                <select value={f.drone_id || usable[0]?.id || ""} onChange={set("drone_id")} required>
                  {usable.map((d) => <option key={d.id} value={d.id}>{d.registration_number} — {d.model} (до {d.max_payload_g} г)</option>)}
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
            <RouteMap route={preview} height={200} />
            <div className="form grid2">
              <label className="span2">Точка А (отправление)
                <select onChange={(e) => { const p = PRESETS[Number(e.target.value)]; idemKey.current = newIdempotencyKey();
                  setF((s) => ({ ...s, origin_address: p.name, origin_lat: p.lat, origin_lon: p.lon })); }}
                  value={PRESETS.findIndex((p) => p.name === f.origin_address)}>
                  {PRESETS.map((p, i) => <option key={p.name} value={i}>{p.name}</option>)}
                  <option value={-1}>Другая точка (координаты ниже)</option>
                </select>
              </label>
              <label>Широта А<input type="number" step="0.0001" value={f.origin_lat} onChange={set("origin_lat")} /></label>
              <label>Долгота А<input type="number" step="0.0001" value={f.origin_lon} onChange={set("origin_lon")} /></label>
              <label className="span2">Адрес А<input value={f.origin_address} onChange={set("origin_address")} /></label>
              <label className="span2">Точка Б (получение)
                <select onChange={(e) => { const p = PRESETS[Number(e.target.value)]; idemKey.current = newIdempotencyKey();
                  setF((s) => ({ ...s, dest_address: p.name, dest_lat: p.lat, dest_lon: p.lon })); }}
                  value={PRESETS.findIndex((p) => p.name === f.dest_address)}>
                  {PRESETS.map((p, i) => <option key={p.name} value={i}>{p.name}</option>)}
                  <option value={-1}>Другая точка (координаты ниже)</option>
                </select>
              </label>
              <label>Широта Б<input type="number" step="0.0001" value={f.dest_lat} onChange={set("dest_lat")} /></label>
              <label>Долгота Б<input type="number" step="0.0001" value={f.dest_lon} onChange={set("dest_lon")} /></label>
              <label className="span2">Адрес Б<input value={f.dest_address} onChange={set("dest_address")} /></label>
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
