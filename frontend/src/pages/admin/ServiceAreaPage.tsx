import { useEffect, useState } from "react";
import { ApiError, api, errorText } from "../../api";
import { MapPointsEditor, type MapPin } from "../../components/MapPointsEditor";
import { Alert, Card, KV, Pending, Tone } from "../../components/ui";
import { fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { invalidateServiceArea, inArea, type Area, type ServiceAreaInfo } from "../../serviceArea";
import type { Fleet } from "../../types";

type Msg = { tone: string; text: string } | null;
interface OutsideFleet { id: string; name: string; address: string }

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;

/** Зона обслуживания: прямоугольник на карте, внутри которого должны быть базы парков и точки
 *  маршрутов. Углы перетаскиваются; границы можно ввести числами. */
export function ServiceAreaPage() {
  const info = useLive(() => api.get<ServiceAreaInfo>("/api/v1/service-area"), []);
  const fleets = useLive(() => api.get<{ items: Fleet[] }>("/api/v1/fleets"), []);
  const [draft, setDraft] = useState<Area | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [outside, setOutside] = useState<OutsideFleet[]>([]);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState("nw");
  const [fit, setFit] = useState(0);               // перерисовать карту по новым границам (не при перетаскивании)

  useEffect(() => {
    if (info.data && !draft) setDraft({ south: info.data.south, west: info.data.west, north: info.data.north, east: info.data.east });
  }, [info.data, draft]);
  if (!info.data || !draft) return <Pending />;
  const cur = info.data;
  const bases = (fleets.data?.items ?? []).filter((f) => f.active);
  const changed = ["south", "west", "north", "east"].some((k) => draft[k as keyof Area] !== cur[k as keyof Area]);
  const valid = draft.south < draft.north && draft.west < draft.east;
  const set = (patch: Partial<Area>) => {
    setOutside([]);
    setDraft({ ...draft, ...patch });
  };

  const pins: MapPin[] = [
    { key: "nw", label: "СЗ", title: "Северо-западный угол зоны", point: { lat: draft.north, lon: draft.west }, className: "map-base-pin" },
    { key: "se", label: "ЮВ", title: "Юго-восточный угол зоны", point: { lat: draft.south, lon: draft.east }, className: "map-base-pin" },
    ...bases.map((f) => ({ key: `fleet-${f.id}`, label: "А", title: `База парка «${f.name}»`, point: f.base,
                           className: inArea(draft, f.base) ? "map-pickup" : "map-b", locked: true })),
  ];
  function moveCorner(key: string, p: { lat: number; lon: number }) {
    // углы не могут «перехлестнуться»: север всегда выше юга, восток правее запада
    if (key === "nw") set({ north: round6(Math.max(p.lat, draft!.south + 0.001)), west: round6(Math.min(p.lon, draft!.east - 0.001)) });
    if (key === "se") set({ south: round6(Math.min(p.lat, draft!.north - 0.001)), east: round6(Math.max(p.lon, draft!.west + 0.001)) });
  }
  function coverFleets() {
    if (!bases.length) return;
    const m = 0.05;                                  // запас ~5 км вокруг баз
    const lats = bases.map((f) => f.base.lat), lons = bases.map((f) => f.base.lon);
    set({ south: round6(Math.min(draft!.south, ...lats.map((v) => v - m))), north: round6(Math.max(draft!.north, ...lats.map((v) => v + m))),
          west: round6(Math.min(draft!.west, ...lons.map((v) => v - m))), east: round6(Math.max(draft!.east, ...lons.map((v) => v + m))) });
    setFit((n) => n + 1);
  }
  async function save(confirm = false) {
    setBusy(true);
    setMsg(null);
    try {
      await api.put("/api/v1/admin/service-area", { ...draft, confirm });
      setOutside([]);
      setMsg({ tone: "ok", text: "Зона обслуживания сохранена — действует для новых парков и проверки новых заказов" });
      invalidateServiceArea();
      info.reload();
    } catch (err) {
      if (err instanceof ApiError && err.code === "fleets_outside") {
        setOutside((err.details.fleets as OutsideFleet[]) ?? []);
      } else {
        setMsg({ tone: "danger", text: errorText(err) });
      }
    } finally {
      setBusy(false);
    }
  }
  async function reset() {
    if (!confirm("Вернуть зону из настроек сервера (FPV_SERVICE_AREA)?")) return;
    setBusy(true);
    try {
      const d = await api.del<ServiceAreaInfo>("/api/v1/admin/service-area");
      setDraft({ south: d.south, west: d.west, north: d.north, east: d.east });
      setFit((n) => n + 1);
      setMsg({ tone: "ok", text: "Зона снова берётся из настроек сервера" });
      invalidateServiceArea();
      info.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }
  const num = (k: keyof Area, label: string, min: number, max: number) => (
    <label>{label}<input type="number" step="0.0001" min={min} max={max} value={draft[k]}
                         onChange={(e) => set({ [k]: Number(e.target.value) } as Partial<Area>)}
                         onBlur={() => setFit((n) => n + 1)} /></label>
  );

  return (
    <div className="stack">
      <h1>Зона обслуживания</h1>
      <p className="muted">Внутри этого прямоугольника должны находиться базы парков и все точки маршрутов заказов.
        Изменение действует для новых парков, переноса баз и проверки новых заказов; уже оформленные заказы не меняются.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {outside.length > 0 && (
        <Alert tone="warn">
          <div>Базы этих парков окажутся вне зоны — заказы на их дроны не пройдут проверку, пока техники не перенесут базы:</div>
          <ul>{outside.map((f) => <li key={f.id}><b>{f.name}</b> — {f.address}</li>)}</ul>
          <div className="row">
            <button className="btn danger" disabled={busy} onClick={() => void save(true)}>Всё равно сохранить</button>
            <button className="btn" onClick={coverFleets}>Расширить зону, чтобы охватить базы</button>
          </div>
        </Alert>
      )}
      <div className="grid2 area-grid">
        <Card title="Зона на карте">
          <MapPointsEditor pins={pins} onChange={moveCorner} active={active} onActiveChange={setActive} area={valid ? draft : null}
                           showServiceArea={false} route={false} height={420} fitKey={fit}
                           emptyHint="Карт нет — задайте границы числами справа (карты подключает администратор: «Карты»)." />
          <p className="muted small">Перетащите углы «СЗ» и «ЮВ». Метки «А» — базы парков: оранжевые внутри зоны, красные — снаружи.</p>
        </Card>
        <Card title="Границы">
          <KV items={[
            ["Сейчас действует", cur.source === "admin"
              ? <>заданная администратором{cur.updated_by ? ` (${cur.updated_by.display_name}, ${fmtDate(cur.updated_at)})` : ""}</>
              : <Tone tone="muted">из настроек сервера (FPV_SERVICE_AREA)</Tone>],
          ]} />
          <div className="form grid2">
            {num("north", "Север (широта)", -85, 85)}
            {num("south", "Юг (широта)", -85, 85)}
            {num("west", "Запад (долгота)", -180, 180)}
            {num("east", "Восток (долгота)", -180, 180)}
          </div>
          {!valid && <Alert>Север должен быть больше юга, восток — больше запада.</Alert>}
          <p className="muted small">≈ {((draft.north - draft.south) * 111).toFixed(0)} × {((draft.east - draft.west) * 111 *
            Math.cos(((draft.north + draft.south) / 2) * Math.PI / 180)).toFixed(0)} км.
            Парков: {bases.length}, вне зоны: {bases.filter((f) => !inArea(draft, f.base)).length}.</p>
          <div className="row wrap">
            <button className="btn primary" disabled={busy || !valid || !changed} onClick={() => void save(false)}>Сохранить зону</button>
            <button className="btn" disabled={busy || !changed} onClick={() => { setOutside([]); setDraft({ south: cur.south, west: cur.west, north: cur.north, east: cur.east }); setFit((n) => n + 1); }}>Отменить изменения</button>
            {bases.length > 0 && <button className="btn" disabled={busy} onClick={coverFleets}>Охватить все базы парков</button>}
            {cur.source === "admin" && <button className="btn ghost" disabled={busy} onClick={() => void reset()}>Вернуть из настроек сервера</button>}
          </div>
        </Card>
      </div>
    </div>
  );
}
