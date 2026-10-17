import { useState } from "react";
import { ApiError, api, errorText } from "../../api";
import { MapPointsEditor, type MapPin } from "../../components/MapPointsEditor";
import { Alert, Card, Empty, Pending, Tone } from "../../components/ui";
import { fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { invalidateZones, inArea, type Area } from "../../serviceArea";
import type { Point, UserBrief } from "../../types";

// Зоны обслуживания: администратор создаёт сколько угодно зон и назначает в каждую любое число
// техников (техник может работать в нескольких зонах). Зона без техников открыта всем техникам.

type Msg = { tone: string; text: string } | null;
interface ZoneFleet { id: string; name: string; active: boolean; technician: UserBrief | null; base: Point & { address: string } }
interface ZoneRow extends Area {
  id: string; name: string; active: boolean; open_to_all: boolean; technicians: (UserBrief & { username?: string })[];
  fleets: ZoneFleet[]; updated_at: string | null; updated_by: UserBrief | null;
}
interface Tech { id: string; display_name: string; username: string }
interface Draft extends Area { id: string | null; name: string }
interface OutsideFleet { id: string; name: string; address: string }

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
const km = (a: Area) => `${((a.north - a.south) * 111).toFixed(0)} × ${((a.east - a.west) * 111 *
  Math.cos(((a.north + a.south) / 2) * Math.PI / 180)).toFixed(0)} км`;

export function ZonesPage() {
  const data = useLive(() => api.get<{ items: ZoneRow[]; technicians: Tech[] }>("/api/v1/service-zones"), [],
    (m) => m.kind === "platform.zones_changed");
  const [msg, setMsg] = useState<Msg>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [outside, setOutside] = useState<{ fleets: OutsideFleet[]; retry: () => Promise<void> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [fit, setFit] = useState(0);
  const [active, setActive] = useState("nw");
  if (!data.data) return <Pending />;
  const zones = data.data.items;
  const techs = data.data.technicians;
  const allFleets = zones.flatMap((z) => z.fleets);

  async function run(fn: () => Promise<unknown>, ok: string, onConflict?: (retry: () => Promise<void>) => () => Promise<void>) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setOutside(null);
      setMsg({ tone: "ok", text: ok });
      invalidateZones();
      data.reload();
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.code === "fleets_outside" && onConflict) {
        setOutside({ fleets: (err.details.fleets as OutsideFleet[]) ?? [], retry: onConflict(async () => undefined) });
      } else {
        setMsg({ tone: "danger", text: errorText(err) });
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  function edit(z: ZoneRow | null) {
    setOutside(null);
    if (z) setDraft({ id: z.id, name: z.name, south: z.south, west: z.west, north: z.north, east: z.east });
    else {
      // новая зона — рядом с первой существующей, чтобы её было видно на карте
      const base = zones[0] ?? { south: 55.55, west: 37.35, north: 55.95, east: 37.9 };
      setDraft({ id: null, name: "", south: base.south, west: base.west, north: base.north, east: base.east });
    }
    setActive("nw");
    setFit((n) => n + 1);
  }
  function setD(patch: Partial<Draft>) {
    setOutside(null);
    setDraft((d) => (d ? { ...d, ...patch } : d));
  }
  function moveCorner(key: string, p: { lat: number; lon: number }) {
    if (!draft) return;
    if (key === "nw") setD({ north: round6(Math.max(p.lat, draft.south + 0.001)), west: round6(Math.min(p.lon, draft.east - 0.001)) });
    if (key === "se") setD({ south: round6(Math.min(p.lat, draft.north - 0.001)), east: round6(Math.max(p.lon, draft.west + 0.001)) });
  }
  async function saveDraft(confirm = false) {
    if (!draft) return;
    const body = { name: draft.name.trim(), south: draft.south, west: draft.west, north: draft.north, east: draft.east, confirm };
    const ok = await run(() => draft.id ? api.patch(`/api/v1/admin/service-zones/${draft.id}`, body)
                                        : api.post("/api/v1/admin/service-zones", body),
      draft.id ? `Зона «${body.name}» сохранена` : `Зона «${body.name}» создана — назначьте в неё техников`,
      () => () => saveDraft(true));
    if (ok) setDraft(null);
  }
  const toggle = (z: ZoneRow, confirm = false): Promise<unknown> => run(
    () => api.patch(`/api/v1/admin/service-zones/${z.id}`, { active: !z.active, confirm }),
    z.active ? `Зона «${z.name}» отключена` : `Зона «${z.name}» включена`, () => () => toggle(z, true).then(() => undefined));
  const remove = (z: ZoneRow, confirm = false): Promise<unknown> => run(
    () => api.del(`/api/v1/admin/service-zones/${z.id}${confirm ? "?confirm=true" : ""}`),
    `Зона «${z.name}» удалена`, () => () => remove(z, true).then(() => undefined));
  const assign = (z: ZoneRow, techId: string, on: boolean) => run(
    () => on ? api.put(`/api/v1/admin/service-zones/${z.id}/technicians/${techId}`)
             : api.del(`/api/v1/admin/service-zones/${z.id}/technicians/${techId}`),
    on ? "Техник назначен в зону" : "Техник исключён из зоны");

  const valid = !!draft && draft.south < draft.north && draft.west < draft.east && draft.name.trim().length > 0;
  const pins: MapPin[] = draft ? [
    { key: "nw", label: "СЗ", title: "Северо-западный угол зоны", point: { lat: draft.north, lon: draft.west }, className: "map-base-pin" },
    { key: "se", label: "ЮВ", title: "Юго-восточный угол зоны", point: { lat: draft.south, lon: draft.east }, className: "map-base-pin" },
    ...allFleets.filter((f) => f.active).map((f) => ({ key: `fleet-${f.id}`, label: "А", title: `База парка «${f.name}»`, point: f.base,
      className: inArea(draft, f.base) ? "map-pickup" : "map-b", locked: true })),
  ] : [];

  return (
    <div className="stack">
      <div className="row between">
        <h1>Зоны обслуживания</h1>
        <button className="btn primary" disabled={busy} onClick={() => edit(null)}>+ Новая зона</button>
      </div>
      <p className="muted">Внутри зоны должны находиться базы парков и все точки маршрутов заказов на дроны этих парков.
        Зон может быть сколько угодно; в каждую можно назначить любое число техников, один техник может работать в
        нескольких зонах. Зона без назначенных техников открыта всем техникам. Изменения действуют для новых парков,
        переноса баз и проверки новых заказов.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {outside && (
        <Alert tone="warn">
          <div>Базы этих парков окажутся вне зоны — заказы на их дроны не пройдут проверку, пока техники не перенесут базы
            или парки не попадут в другую зону:</div>
          <ul>{outside.fleets.map((f) => <li key={f.id}><b>{f.name}</b> — {f.address}</li>)}</ul>
          <div className="row">
            <button className="btn danger" disabled={busy} onClick={() => void outside.retry()}>Всё равно продолжить</button>
            <button className="btn" onClick={() => setOutside(null)}>Отмена</button>
          </div>
        </Alert>
      )}

      {draft && (
        <Card title={draft.id ? `Границы зоны «${draft.name}»` : "Новая зона"}>
          <div className="grid2 area-grid">
            <div>
              <MapPointsEditor pins={pins} onChange={moveCorner} active={active} onActiveChange={setActive}
                               area={valid || draft.name.trim() === "" ? draft : null} excludeZoneId={draft.id ?? undefined}
                               route={false} height={420} fitKey={fit}
                               emptyHint="Карт нет — задайте границы числами (карты подключаются в разделе «Карты»)." />
              <p className="muted small">Перетащите углы «СЗ» и «ЮВ». Пунктиром — другие зоны. Метки «А» — базы парков:
                оранжевые внутри этой зоны, красные — снаружи.</p>
            </div>
            <div className="stack">
              <label>Название<input value={draft.name} onChange={(e) => setD({ name: e.target.value })} maxLength={120}
                                    placeholder="Например: Хельсинки" autoFocus={!draft.id} /></label>
              <div className="form grid2">
                {(["north", "south", "west", "east"] as const).map((k) => (
                  <label key={k}>{{ north: "Север (широта)", south: "Юг (широта)", west: "Запад (долгота)", east: "Восток (долгота)" }[k]}
                    <input type="number" step="0.0001" value={draft[k]} onChange={(e) => setD({ [k]: Number(e.target.value) })}
                           onBlur={() => setFit((n) => n + 1)} /></label>
                ))}
              </div>
              {draft.south >= draft.north || draft.west >= draft.east
                ? <Alert>Север должен быть больше юга, восток — больше запада.</Alert>
                : <p className="muted small">≈ {km(draft)}; баз парков внутри: {allFleets.filter((f) => f.active && inArea(draft, f.base)).length}.</p>}
              <div className="row wrap">
                <button className="btn primary" disabled={busy || !valid} onClick={() => void saveDraft(false)}>
                  {draft.id ? "Сохранить зону" : "Создать зону"}</button>
                <button className="btn" disabled={busy} onClick={() => { setDraft(null); setOutside(null); }}>Отмена</button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {zones.length === 0 ? <Card><Empty>Зон нет — без зоны техники не смогут создать парк. Нажмите «+ Новая зона».</Empty></Card> : (
        <div className="zones-grid">
          {zones.map((z) => {
            const assigned = new Set(z.technicians.map((t) => t.id));
            return (
              <Card key={z.id} className={`zone-card ${z.active ? "" : "inactive"}`} title={<>{z.name} {!z.active && <Tone tone="muted">отключена</Tone>}</>}
                    actions={<span className="muted small">{km(z)}</span>}>
                <div className="small muted">Широта {z.south}…{z.north}, долгота {z.west}…{z.east}
                  {z.updated_by && <> · изменил(а) {z.updated_by.display_name}, {fmtDate(z.updated_at)}</>}</div>
                <div>
                  <strong className="small">Техники</strong>
                  <div className="role-chips">
                    {z.technicians.map((t) => (
                      <span key={t.id} className="chip">{t.display_name}
                        <button type="button" aria-label={`Исключить ${t.display_name} из зоны ${z.name}`} disabled={busy}
                                onClick={() => void assign(z, t.id, false)}>×</button></span>
                    ))}
                    {z.open_to_all && <span className="muted small">не назначены — зона открыта всем техникам</span>}
                  </div>
                  <select value="" disabled={busy} aria-label={`Назначить техника в зону ${z.name}`}
                          onChange={(e) => e.target.value && void assign(z, e.target.value, true)}>
                    <option value="">+ назначить техника…</option>
                    {techs.filter((t) => !assigned.has(t.id)).map((t) => <option key={t.id} value={t.id}>{t.display_name} ({t.username})</option>)}
                  </select>
                </div>
                <div className="small">
                  <strong>Парки ({z.fleets.length})</strong>{z.fleets.length === 0 ? <span className="muted"> — нет</span> : (
                    <ul className="zone-fleets">{z.fleets.map((f) => <li key={f.id}>{f.name}
                      <span className="muted"> — {f.technician?.display_name ?? "—"}{f.active ? "" : ", закрыт"}</span></li>)}</ul>)}
                </div>
                <div className="row wrap">
                  <button className="btn small" disabled={busy} onClick={() => edit(z)}>Изменить границы</button>
                  <button className="btn small" disabled={busy} onClick={() => void toggle(z)}>{z.active ? "Отключить" : "Включить"}</button>
                  <button className="btn small danger" disabled={busy}
                          onClick={() => confirm(`Удалить зону «${z.name}»?`) && void remove(z)}>Удалить</button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
