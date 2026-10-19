import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { api, errorText, newIdempotencyKey } from "../../api";
import { MapPointsEditor } from "../../components/MapPointsEditor";
import { MapSourcePicker, preferredMapSource } from "../../components/MapSourcePicker";
import { PRIORITY_LABELS } from "../../components/RepeatBadges";
import { POINT_LETTERS, RouteMap } from "../../components/RouteMap";
import { Alert, Card, KV, Pending } from "../../components/ui";
import { loadCargoTypes, loadConsignees, type CargoType, type Consignee } from "../../catalogs";
import { useLive } from "../../realtime";
import { LOCAL } from "../../tiles";
import type { AvailableDrone, Order, RouteInfo, Stop } from "../../types";

// Оформление заказа по шагам: грузополучатель → груз → БПЛА → маршрут → подтверждение.
// Груз забирается на базе парка дрона (точка А); точки маршрута — «Точка Б», «Точка В», …,
// последняя — грузополучатель, остальные промежуточные. Время доставки и телефон не нужны.

const STEPS = ["Выбор грузополучателя", "Выбор груза", "Выбор БПЛА", "Выбор маршрута", "Подтверждение заказа"];
const MAX_POINTS = 6;
const MAX_CARGO = 10;
const PHOTO_MAX = 15 * 1024 * 1024;

interface CargoDraft {
  cargo_type: string;
  description: string;
  weight_g: number;
  length_mm: number;
  width_mm: number;
  height_mm: number;
}
const newCargo = (type = ""): CargoDraft => ({ cargo_type: type, description: "", weight_g: 300, length_mm: 300, width_mm: 220, height_mm: 30 });

export const pointName = (i: number) => `Точка ${POINT_LETTERS[i] ?? i + 1}`;
const mapAddress = (lat: number, lon: number) => `Точка на карте (${lat.toFixed(5)}, ${lon.toFixed(5)})`;
const isAutoAddress = (a: string) => !a.trim() || a.startsWith("Точка на карте");

function haversine(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const h = Math.sin(r(b.lat - a.lat) / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(r(b.lon - a.lon) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

export function NewOrderPage() {
  const navigate = useNavigate();
  const consignees = useLive(loadConsignees, []);
  const cargoTypes = useLive(loadCargoTypes, []);
  const drones = useLive(() => api.get<{ items: AvailableDrone[] }>("/api/v1/available-drones"), [],
    (m) => m.kind === "drone.updated");
  const [step, setStep] = useState(0);
  const [consigneeId, setConsigneeId] = useState("");
  const [cargo, setCargo] = useState<CargoDraft[]>([newCargo()]);
  const [cargoTouched, setCargoTouched] = useState(false);
  const [droneId, setDroneId] = useState("");
  const [points, setPoints] = useState<Stop[]>([]);
  const [adding, setAdding] = useState(false);
  const [mapSource, setMapSource] = useState(preferredMapSource);
  const [comment, setComment] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [checks, setChecks] = useState<{ code: string; ok: boolean; message: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const idemKey = useRef(newIdempotencyKey());
  useEffect(() => { idemKey.current = newIdempotencyKey(); setChecks(null); },
    [consigneeId, cargo, droneId, points, mapSource, comment, photo]);
  useEffect(() => { window.scrollTo?.({ top: 0 }); }, [step]);

  const types = cargoTypes.data ?? [];
  const consignee = (consignees.data ?? []).find((c) => c.id === consigneeId);
  const typeOf = (code: string) => types.find((c) => c.code === code);
  const drone = (drones.data?.items ?? []).find((d) => d.id === droneId);
  const base = drone?.fleet.base;

  // Рекомендованный груз грузополучателя подставляется, пока заказчик не менял груз сам.
  function chooseConsignee(id: string) {
    setConsigneeId(id);
    const c = (consignees.data ?? []).find((x) => x.id === id);
    if (c?.recommended_cargo && !cargoTouched) setCargo((list) => list.map((x, i) => (i === 0 ? { ...x, cargo_type: c.recommended_cargo! } : x)));
  }
  const editCargo = (i: number, patch: Partial<CargoDraft>) => {
    setCargoTouched(true);
    setCargo((list) => list.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  };
  const effectiveType = (c: CargoDraft) => c.cargo_type || consignee?.recommended_cargo || types[0]?.code || "";

  const addPoint = (p: { lat: number; lon: number }) => {
    if (points.length >= MAX_POINTS) return;
    setPoints((list) => [...list, { lat: p.lat, lon: p.lon, address: mapAddress(p.lat, p.lon), note: "" }]);
    setAdding(false);
  };
  const movePoint = (key: string, p: { lat: number; lon: number }) => {
    const i = Number(key.slice(2));
    setPoints((list) => list.map((x, j) => (j === i
      ? { ...x, lat: p.lat, lon: p.lon, address: isAutoAddress(x.address) ? mapAddress(p.lat, p.lon) : x.address } : x)));
  };
  const editPoint = (i: number, patch: Partial<Stop>) => setPoints((list) => list.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const swap = (i: number, d: number) => setPoints((list) => {
    const next = [...list];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    return next;
  });

  const cargoValid = cargo.length > 0 && cargo.every((c) => {
    const t = typeOf(effectiveType(c));
    return t && c.description.trim().length >= 3 && c.weight_g > 0 && c.weight_g <= t.max_weight_g
      && c.length_mm > 0 && c.width_mm > 0 && c.height_mm > 0;
  });
  const valid = [!!consignee, cargoValid, !!drone, points.length >= 1 && points.every((p) => p.address.trim().length >= 3), true];

  function cancelAll() {
    if (confirm("Отменить оформление заказа? Введённые данные не сохранятся.")) navigate("/");
  }

  async function place() {
    if (!drone || !consignee) return;
    setBusy(true);
    setError("");
    setChecks(null);
    let draft: Order | null = null;
    try {
      if (photo && photo.size > PHOTO_MAX) throw new Error("Фото больше 15 МБ");
      draft = await api.post<Order>("/api/v1/orders", {
        drone_id: drone.id, consignee_id: consignee.id,
        cargo_items: cargo.map((c) => ({ ...c, cargo_type: effectiveType(c), description: c.description.trim() })),
        points: points.map((p) => ({ lat: p.lat, lon: p.lon, address: p.address.trim(), note: p.note ?? "" })),
        map_provider_id: mapSource && mapSource !== LOCAL ? mapSource : null,
        customer_comment: comment.trim(),
      }, { idempotencyKey: idemKey.current });
      if (photo) await api.upload(`/api/v1/orders/${draft.id}/comment-photo`, photo);
      const checked = await api.post<Order>(`/api/v1/orders/${draft.id}/submit`, { expected_version: draft.version },
        { idempotencyKey: `${idemKey.current}-submit` });
      if (checked.status !== "pending_confirmation") {
        setChecks(checked.validation_result?.checks ?? []);
        await api.post(`/api/v1/orders/${draft.id}/cancel`, { reason: "Проверки не пройдены при оформлении" }).catch(() => {});
        return;
      }
      const placed = await api.post<Order>(`/api/v1/orders/${draft.id}/confirm`, { expected_version: checked.version },
        { idempotencyKey: `${idemKey.current}-confirm` });
      navigate(`/orders/${placed.id}`);
    } catch (err) {
      setError(errorText(err));
      if (draft) await api.post(`/api/v1/orders/${draft.id}/cancel`, { reason: "Ошибка при оформлении" }).catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  if (!consignees.data || !cargoTypes.data || !drones.data) return <Pending />;
  const available = drones.data.items;
  const fleets = Array.from(new Map(available.map((d) => [d.fleet.id, d.fleet.name])).entries());
  const preview: RouteInfo | null = base && points.length ? previewRoute(base, points) : null;

  const footer = (
    <div className="wizard-foot">
      {step < 4 ? <button type="button" className="btn" onClick={cancelAll}>Отменить</button>
                : <button type="button" className="btn danger" onClick={cancelAll}>Отменить заказ</button>}
      {step > 0 && <button type="button" className="btn ghost" onClick={() => setStep(step - 1)}>← Назад</button>}
      <span className="spacer" />
      {step < 4
        ? <button type="button" className="btn primary" disabled={!valid[step]} onClick={() => setStep(step + 1)}>Далее →</button>
        : <button type="button" className="btn ok-fill" disabled={busy} onClick={place}>{busy ? "Размещение…" : "Разместить заказ"}</button>}
    </div>
  );

  return (
    <div className="stack wizard">
      <h1>Новый заказ</h1>
      <ol className="wizard-steps" aria-label="Шаги оформления">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "current" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
            <span className="num">{i + 1}</span><span className="name">{s}</span>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <Card title={STEPS[0]}>
          {consignees.data.length === 0 ? <Alert tone="warn">Список грузополучателей пуст — обратитесь к администратору.</Alert> : (
            <div className="form">
              <label>Грузополучатель
                <select value={consigneeId} onChange={(e) => chooseConsignee(e.target.value)} aria-label="Грузополучатель">
                  <option value="" disabled>— выберите грузополучателя —</option>
                  {consignees.data.map((c) => <option key={c.id} value={c.id}>{c.name}{c.kind ? ` (${c.kind})` : ""}</option>)}
                </select>
              </label>
              {consignee && <ConsigneeInfo c={consignee} />}
            </div>
          )}
        </Card>
      )}

      {step === 1 && (
        <Card title={STEPS[1]}>
          <div className="stack">
            {cargo.map((c, i) => {
              const t = typeOf(effectiveType(c));
              return (
                <div key={i} className="cargo-item form grid2">
                  <div className="span2 row between">
                    <strong>Груз {i + 1}</strong>
                    {cargo.length > 1 && <button type="button" className="btn small ghost" onClick={() => setCargo((l) => l.filter((_, j) => j !== i))}
                      aria-label={`Удалить груз ${i + 1}`}>✕ Удалить</button>}
                  </div>
                  <label>Тип груза
                    <select value={effectiveType(c)} onChange={(e) => editCargo(i, { cargo_type: e.target.value })} aria-label={`Тип груза ${i + 1}`}>
                      {types.map((x: CargoType) => (
                        <option key={x.code} value={x.code}>{x.name}{x.code === consignee?.recommended_cargo ? " — рекомендован" : ""}</option>))}
                    </select>
                  </label>
                  <label>Масса, г{t && <span className="small muted"> (не более {t.max_weight_g})</span>}
                    <input type="number" min={1} max={t?.max_weight_g} value={c.weight_g}
                           onChange={(e) => editCargo(i, { weight_g: Number(e.target.value) })} aria-label={`Масса груза ${i + 1}`} /></label>
                  <label className="span2">Описание груза<textarea value={c.description} minLength={3}
                    onChange={(e) => editCargo(i, { description: e.target.value })} aria-label={`Описание груза ${i + 1}`} /></label>
                  <div className="row span2 dims">
                    <label>Д, мм<input type="number" min={1} value={c.length_mm} onChange={(e) => editCargo(i, { length_mm: Number(e.target.value) })} /></label>
                    <label>Ш, мм<input type="number" min={1} value={c.width_mm} onChange={(e) => editCargo(i, { width_mm: Number(e.target.value) })} /></label>
                    <label>В, мм<input type="number" min={1} value={c.height_mm} onChange={(e) => editCargo(i, { height_mm: Number(e.target.value) })} /></label>
                  </div>
                </div>
              );
            })}
            <div className="row between wrap">
              <button type="button" className="btn small" onClick={() => setCargo((l) => [...l, newCargo()])} disabled={cargo.length >= MAX_CARGO}>
                + Добавить груз</button>
              <span className="small muted">Грузов: {cargo.length}, общая масса {cargo.reduce((n, c) => n + (Number(c.weight_g) || 0), 0)} г</span>
            </div>
            {!cargoValid && <p className="small muted">Заполните описание (не короче 3 символов) и массу каждого груза.</p>}
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card title={STEPS[2]}>
          {available.length === 0 ? <Alert tone="warn">Сейчас нет дронов, открытых для заказов.</Alert> : (
            <div className="form">
              <label>БПЛА (из открытых парков)
                <select value={droneId} onChange={(e) => { setDroneId(e.target.value); setPoints([]); }} aria-label="БПЛА">
                  <option value="" disabled>— выберите БПЛА —</option>
                  {fleets.map(([id, name]) => (
                    <optgroup key={id} label={`Парк «${name}»`}>
                      {available.filter((d) => d.fleet.id === id).map((d) => (
                        <option key={d.id} value={d.id}>{d.registration_number} — {d.manufacturer} {d.model}{d.busy ? " (занят — заказ встанет в очередь)" : ""}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              {drone && <DroneInfo d={drone} />}
            </div>
          )}
        </Card>
      )}

      {step === 3 && drone && (
        <Card title={STEPS[3]}>
          <div className="stack">
            <MapPointsEditor source={mapSource} onSourceChange={setMapSource} showSourceSelect={false} height={380}
              zoneIds={drone.fleet.zone_id ? [drone.fleet.zone_id] : null} fitKey={drone.id}
              onChange={movePoint} clickToPlace={false} adding={adding} onMapClick={adding ? addPoint : undefined}
              emptyHint="Карт района нет — выберите онлайн-карту внизу страницы или укажите координаты точек вручную."
              pins={[
                ...(base ? [{ key: "base", label: "А", title: "А — база парка (груз)", point: base, className: "map-base-pin", locked: true }] : []),
                ...points.map((p, i) => ({ key: `p-${i}`, label: POINT_LETTERS[i] ?? String(i + 1), title: pointName(i),
                  point: { lat: p.lat, lon: p.lon }, className: i === points.length - 1 ? "map-b" : "map-stop" })),
              ]} />
            <div className="row wrap">
              <button type="button" className={`btn ${adding ? "primary" : ""}`} onClick={() => setAdding(!adding)}
                      disabled={points.length >= MAX_POINTS} aria-pressed={adding}>
                {adding ? "Щёлкните по карте…" : "+ Добавить точку"}</button>
              <span className="small muted">{points.length === 0
                ? "Нажмите «Добавить точку» и щёлкните по карте. Последняя точка — грузополучатель, остальные — промежуточные."
                : `Точек: ${points.length} из ${MAX_POINTS}. Метки можно перетаскивать.`}</span>
            </div>
            <div className="points-list">
              <div className="point-row base"><span className="pin-dot map-base-pin">А</span>
                <span>База парка «{drone.fleet.name}»: {base?.address ?? "—"} — отсюда дрон вылетает с грузом</span></div>
              {points.map((p, i) => (
                <div key={i} className="point-row">
                  <span className={`pin-dot ${i === points.length - 1 ? "map-b" : "map-stop"}`} aria-hidden>{POINT_LETTERS[i]}</span>
                  <div className="point-fields">
                    <strong>{pointName(i)}{i === points.length - 1 ? " — грузополучатель" : " — промежуточная"}</strong>
                    <input value={p.address} onChange={(e) => editPoint(i, { address: e.target.value })} aria-label={`Адрес: ${pointName(i)}`} placeholder="Адрес" />
                    <input value={p.note ?? ""} onChange={(e) => editPoint(i, { note: e.target.value })} maxLength={200}
                           aria-label={`Комментарий: ${pointName(i)}`} placeholder="Что сделать (необязательно)" />
                    <div className="row">
                      <input type="number" step="0.0001" value={p.lat} onChange={(e) => editPoint(i, { lat: Number(e.target.value) })} aria-label={`Широта: ${pointName(i)}`} />
                      <input type="number" step="0.0001" value={p.lon} onChange={(e) => editPoint(i, { lon: Number(e.target.value) })} aria-label={`Долгота: ${pointName(i)}`} />
                    </div>
                  </div>
                  <span className="row">
                    <button type="button" className="btn small ghost" disabled={i === 0} onClick={() => swap(i, -1)} aria-label={`${pointName(i)} раньше`}>↑</button>
                    <button type="button" className="btn small ghost" disabled={i === points.length - 1} onClick={() => swap(i, 1)} aria-label={`${pointName(i)} позже`}>↓</button>
                    <button type="button" className="btn small ghost" onClick={() => setPoints((l) => l.filter((_, j) => j !== i))} aria-label={`Удалить: ${pointName(i)}`}>✕</button>
                  </span>
                </div>
              ))}
            </div>
            <MapSourcePicker value={mapSource} onChange={setMapSource} />
          </div>
        </Card>
      )}

      {step === 4 && drone && consignee && (
        <div className="stack">
          {preview && <div className="card wide-map"><RouteMap route={preview} height={420} providerId={mapSource && mapSource !== LOCAL ? mapSource : null} /></div>}
          <div className="grid2">
            <Card title="Маршрут">
              <KV items={[
                ["А — база парка", base?.address ?? "—"],
                ...points.map((p, i) => [`${pointName(i)}${i === points.length - 1 ? " (грузополучатель)" : ""}`,
                  p.note ? `${p.address} — ${p.note}` : p.address] as [string, ReactNode]),
                ["Расстояние (оценка)", preview ? `${(preview.distance_m / 1000).toFixed(2)} км` : "—"],
              ]} />
            </Card>
            <Card title="Грузополучатель"><ConsigneeInfo c={consignee} /></Card>
            <Card title="Груз">
              <KV items={cargo.map((c, i) => [`Груз ${i + 1}`,
                `${typeOf(effectiveType(c))?.name ?? "—"}: ${c.description}, ${c.weight_g} г, ${c.length_mm}×${c.width_mm}×${c.height_mm} мм`] as [string, ReactNode])} />
            </Card>
            <Card title="БПЛА"><DroneInfo d={drone} /></Card>
          </div>
          <Card title="Комментарий к заказу (необязательно)">
            <div className="form">
              <label>Комментарий<textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000}
                placeholder="Появится в переписке по заказу после размещения" /></label>
              <label>Фото (PNG, JPEG или WebP, до 15 МБ)
                <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} /></label>
              {photo && <span className="small">Приложено: {photo.name} <button type="button" className="btn small ghost" onClick={() => setPhoto(null)}>✕</button></span>}
            </div>
          </Card>
          {checks && (
            <Alert tone="danger">Заказ не прошёл проверки — исправьте данные на предыдущих шагах:
              <ul className="checks">{checks.filter((c) => !c.ok).map((c) => <li key={c.code} className="fail">✕ {c.message}</li>)}</ul></Alert>
          )}
          <p className="small muted">После размещения заказ согласует координатор, затем его примет оператор. Время доставки не задаётся —
            заказ выполняется как можно скорее в порядке приоритета.</p>
        </div>
      )}

      <Alert>{error}</Alert>
      {footer}
    </div>
  );
}

/** Маршрут для предпросмотра (до создания заказа): груз на базе, точки по порядку. */
function previewRoute(base: { lat: number; lon: number; address?: string }, points: Stop[]): RouteInfo {
  const chain = [base, ...points];
  const dist = chain.slice(1).reduce((n, p, i) => n + haversine(chain[i], p), 0);
  const last = points[points.length - 1];
  return {
    base, origin: base, destination: last, stops: points.slice(0, -1), pickup_at_base: true,
    distance_m: Math.round(dist), return_m: Math.round(haversine(last, base)), max_altitude_m: 120,
    checkpoints: [], approval_status: "draft", approval_notes: [],
  };
}

export function ConsigneeInfo({ c }: { c: Pick<Consignee, "name" | "kind" | "location" | "priority" | "priority_label" | "comment" | "recommended_cargo_name"> }) {
  return (
    <KV items={[
      ["Название", c.name],
      ["Тип", c.kind || "—"],
      ["Местоположение", c.location || "—"],
      ["Рекомендованный груз", c.recommended_cargo_name ?? "—"],
      ["Приоритет", c.priority_label ?? PRIORITY_LABELS[c.priority]],
      ["Комментарий", c.comment || "—"],
    ]} />
  );
}

function DroneInfo({ d }: { d: AvailableDrone }) {
  return (
    <KV items={[
      ["Модель", `${d.manufacturer} ${d.model}`],
      ["Учётный номер", d.registration_number],
      ["Парк и база (А)", `«${d.fleet.name}» — ${d.fleet.base.address ?? ""}`],
      ["Грузоподъёмность", `${d.max_payload_g} г`],
      ["Дальность", `${(d.max_range_m / 1000).toFixed(1)} км`],
      ["Тип связи / конструкция / двигатель", [d.link_type, d.construction, d.engine_type].map((x) => x || "—").join(" / ")],
      ["Связь сейчас", d.link_status === "online" ? "на связи" : "не на связи"],
      ["Очередь", d.busy ? "дрон выполняет другой заказ — ваш встанет в очередь" : "свободен"],
    ]} />
  );
}
