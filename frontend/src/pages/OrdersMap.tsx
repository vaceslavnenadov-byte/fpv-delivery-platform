import { useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode, type WheelEvent } from "react";
import { api, errorText } from "../api";
import { appUrl } from "../base";
import { useAuth } from "../auth";
import { RepeatBadges, priorityClass } from "../components/RepeatBadges";
import { Alert, Badge, Pending } from "../components/ui";
import { loadMaps, onMapsChanged } from "../maps";
import { useLive, realtime } from "../realtime";
import { LOCAL, SCHEME, TILE, fitView, loadProviders, onProvidersChanged, project, tilesFor, unproject,
         type MapProviderInfo, type View } from "../tiles";
import type { MapLayer, Point } from "../types";

// Карта заказов (координатор, администратор): все заказы в работе — маршруты, точки доставки и
// дроны на управлении операторов по последней телеметрии. Только наблюдение: полётом платформа
// не управляет. Обновляется каждые несколько секунд.

interface Person { id: string; name: string }
interface MapOrder {
  id: string;
  number: number;
  status: string;
  status_label: string;
  priority_level: "low" | "normal" | "high" | "highest";
  repeat: boolean;
  operator: Person | null;
  offered_operator?: Person | null;
  customer: Person | null;
  coordinator: Person | null;
  drone: { id: string; model: string; manufacturer: string; registration_number: string } | null;
  cargo: { name: string; description: string; weight_g: number }[];
  cargo_weight_g: number;
  consignee: { name: string | null; location: string | null; kind: string | null };
  route: {
    base: (Point & { address?: string }) | null;
    origin: Point & { address?: string };
    stops: (Point & { address?: string })[];
    destination: Point & { address?: string };
    pickup_at_base: boolean;
  } | null;
  operated: boolean;
  position: { lat: number; lon: number; heading_deg: number | null; alt_m: number | null; speed_mps: number | null;
              battery_pct: number | null; recorded_at: string } | null;
  trail: [number, number][];
}

const REFRESH_MS = 4000;
const SOURCE_PREF = "fpv.ordersMap.source";
const readPref = () => { try { return localStorage.getItem(SOURCE_PREF) ?? ""; } catch { return ""; } };
const savePref = (v: string) => { try { localStorage.setItem(SOURCE_PREF, v); } catch { /* только на сессию */ } };

function routePoints(o: MapOrder): Point[] {
  const r = o.route;
  if (!r) return [];
  return [...(r.base ? [r.base] : []), ...(r.pickup_at_base ? [] : [r.origin]), ...r.stops, r.destination];
}

function orderPoints(o: MapOrder): Point[] {
  return [...routePoints(o), ...(o.position ? [o.position] : [])];
}

export function OrdersMapPage({ standalone = false }: { standalone?: boolean }) {
  const { can } = useAuth();
  useEffect(() => {
    if (!standalone) return;
    realtime.start();
    return () => realtime.stop();
  }, [standalone]);
  const data = useLive(() => api.get<{ items: MapOrder[] }>("/api/v1/orders/map"), [],
    (m) => m.kind.startsWith("order."));
  useEffect(() => {
    const t = setInterval(() => data.reload(), REFRESH_MS);
    return () => clearInterval(t);
  }, [data.reload]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [selected, setSelected] = useState<string | null>(null);
  const [popup, setPopup] = useState<{ id: string; kind: "drone" | "dest" } | null>(null);
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  if (!can("order.map")) return <Alert tone="warn">Карта заказов доступна координатору и администратору.</Alert>;
  if (data.error) return <Alert>{errorText(data.error)}</Alert>;
  if (!data.data) return <Pending />;
  const items = data.data.items;
  // Выбранный на карте заказ — первым в списке.
  const list = selected ? [...items.filter((o) => o.id === selected), ...items.filter((o) => o.id !== selected)] : items;

  const pick = (id: string, kind: "drone" | "dest") => { setSelected(id); setPopup({ id, kind }); };
  const showOnMap = (id: string) => { setSelected(id); setPopup(null); setFocus((f) => ({ id, n: (f?.n ?? 0) + 1 })); };
  const openOrder = (id: string) => {
    const w = window.open(appUrl(`/orders/${id}`), `fpv-order-${id}`, "popup,width=1280,height=900,left=60,top=40");
    w?.focus();
  };
  const fullscreen = () => {
    const el = wrap.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };
  const popout = () => {
    const w = window.open(appUrl("/map/window"), "fpv-orders-map", "popup,width=1600,height=1000,left=20,top=20");
    w?.focus();
  };

  return (
    <div className={`orders-map-page ${standalone ? "standalone" : ""}`}>
      {!standalone && <h1>Карта заказов</h1>}
      <div className="orders-map-layout">
        <div className="orders-map-wrap" ref={wrap}>
          <OrdersMapView items={items} selected={selected} popup={popup} focus={focus} onPick={pick}
                         onClosePopup={() => setPopup(null)} onOpenOrder={openOrder}
                         toolbar={<>
                           <button type="button" className="btn small" onClick={fullscreen} title="На весь экран">⛶ На весь экран</button>
                           {!standalone && <button type="button" className="btn small" onClick={popout} title="Открыть в отдельном окне">⧉ В отдельном окне</button>}
                         </>} />
        </div>
        <aside className="orders-map-list" aria-label="Заказы на карте">
          <div className="row between"><strong>Заказы ({items.length})</strong>
            <span className="small muted">обновление каждые {REFRESH_MS / 1000} с</span></div>
          {list.length === 0 && <p className="empty">Заказов в работе нет</p>}
          {list.map((o) => (
            <div key={o.id} className={`map-list-item ${o.id === selected ? "selected" : ""} ${priorityClass({ ...o, repeat_of: null })}`}>
              <div className="row between">
                <strong>№{o.number}</strong>
                <Badge kind="order" value={o.status} />
              </div>
              <RepeatBadges o={{ priority_level: o.priority_level, repeat_of: o.repeat ? { id: "", number: null } : null }} />
              <div className="small">{o.consignee.name ?? "—"} · {o.drone?.registration_number ?? "—"}
                {o.operated && <> · {o.position ? "есть телеметрия" : "нет телеметрии"}</>}</div>
              <div className="row">
                <button type="button" className="btn small" onClick={() => showOnMap(o.id)}>Показать на карте</button>
                <button type="button" className="btn small" onClick={() => openOrder(o.id)}>Открыть заказ</button>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}

function OrdersMapView({ items, selected, popup, focus, onPick, onClosePopup, onOpenOrder, toolbar }: {
  items: MapOrder[];
  selected: string | null;
  popup: { id: string; kind: "drone" | "dest" } | null;
  focus: { id: string; n: number } | null;
  onPick: (id: string, kind: "drone" | "dest") => void;
  onClosePopup: () => void;
  onOpenOrder: (id: string) => void;
  toolbar: ReactNode;
}) {
  const [providers, setProviders] = useState<MapProviderInfo[]>([]);
  const [layers, setLayers] = useState<MapLayer[]>([]);
  const [source, setSource] = useState(readPref);
  const [size, setSize] = useState({ W: 900, H: 600 });
  const [view, setView] = useState<View | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const pan = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => Promise.all([loadProviders(), loadMaps()]).then(([p, l]) => {
      if (!alive) return;
      setProviders(p);
      setLayers(l.filter((m) => m.active));
    });
    load();
    const off1 = onProvidersChanged(load);
    const off2 = onMapsChanged(load);
    return () => { alive = false; off1(); off2(); };
  }, []);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ W: Math.max(200, el.clientWidth), H: Math.max(200, el.clientHeight) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const sources = [...providers.map((p) => ({ id: p.id, name: p.name })),
                   ...layers.map((l) => ({ id: `${LOCAL}:${l.id}`, name: `Карта района: ${l.name}` })),
                   { id: SCHEME, name: "Схема" }];
  const effective = sources.some((s) => s.id === source) ? source : sources[0]?.id ?? SCHEME;
  const provider = providers.find((p) => p.id === effective) ?? null;
  const layer = effective.startsWith(`${LOCAL}:`) ? layers.find((l) => `${LOCAL}:${l.id}` === effective) ?? null : null;
  const proj = provider?.projection ?? "3857";
  const minZ = provider?.min_zoom ?? 3, maxZ = provider?.max_zoom ?? 19;
  const { W, H } = size;

  const all = useMemo(() => items.flatMap(orderPoints), [items]);
  const fit = (pts: Point[]) => setView(fitView(pts.length ? pts : all, W, H, 60, proj, minZ, Math.min(maxZ, 17), 15));
  // первый показ, смена карты и размера — все заказы; «Показать на карте» — один заказ
  const fitted = useRef("");
  useEffect(() => {
    const key = `${effective}|${W}x${H}|${all.length > 0}`;
    if (fitted.current === key && view) return;
    fitted.current = key;
    fit(layer && all.length === 0 ? [{ lat: layer.bounds.north, lon: layer.bounds.west }, { lat: layer.bounds.south, lon: layer.bounds.east }] : all);
  }, [effective, W, H, all.length > 0]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!focus) return;
    const o = items.find((x) => x.id === focus.id);
    if (o) fit(orderPoints(o));
  }, [focus]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!view) return <div className="orders-map"><div className="om-canvas" ref={box}><Pending /></div></div>;
  const left = view.cx - W / 2, top = view.cy - H / 2;
  const xy = (p: Point) => { const q = project(p, view.z, proj); return [q.x - left, q.y - top] as const; };

  function zoom(dz: number, ax = W / 2, ay = H / 2) {
    if (!view) return;
    const z = Math.max(minZ, Math.min(maxZ, view.z + dz));
    const at = unproject(left + ax, top + ay, view.z, proj);
    const q = project(at, z, proj);
    setView({ z, cx: q.x - ax + W / 2, cy: q.y - ay + H / 2 });
  }
  const local = (e: { clientX: number; clientY: number }) => {
    const r = box.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };
  function down(e: PointerEvent<SVGSVGElement>) {
    if (e.button !== 0 || (e.target as Element).closest(".om-marker")) return;
    const [x, y] = local(e);
    pan.current = { x, y, cx: view!.cx, cy: view!.cy, moved: false };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent<SVGSVGElement>) {
    const s = pan.current;
    if (!s) return;
    const [x, y] = local(e);
    if (!s.moved && Math.hypot(x - s.x, y - s.y) < 3) return;
    s.moved = true;
    setView({ ...view!, cx: s.cx - (x - s.x), cy: s.cy - (y - s.y) });
  }
  function up(e: PointerEvent<SVGSVGElement>) {
    if ((e.currentTarget as SVGSVGElement).hasPointerCapture(e.pointerId)) (e.currentTarget as SVGSVGElement).releasePointerCapture(e.pointerId);
    pan.current = null;
  }
  function wheel(e: WheelEvent<SVGSVGElement>) {
    const [x, y] = local(e);
    zoom(e.deltaY < 0 ? 0.5 : -0.5, x, y);
  }

  const tiles = provider ? tilesFor(view, W, H, provider) : [];
  const grid = Math.max(40, TILE * 2 ** (view.z - Math.floor(view.z)) / 2);
  const p = popup ? items.find((o) => o.id === popup.id) : null;
  const popupAt = p ? (popup!.kind === "drone" && p.position ? xy(p.position) : p.route ? xy(p.route.destination) : null) : null;
  // заказ без координат дрона: дрон на управлении показываем на базе (бледным)
  const droneAt = (o: MapOrder): Point | null => (o.position ? o.position : o.operated && o.route?.base ? o.route.base : null);

  return (
    <div className="orders-map">
     <div className="om-canvas" ref={box}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className={`om-svg ${provider || layer ? "with-base" : ""}`}
           role="application" aria-label="Карта заказов: перетаскивайте карту, колесо — масштаб"
           onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onWheel={wheel}>
        <rect x={0} y={0} width={W} height={H} className="om-bg" />
        {tiles.map((t) => <image key={t.key} href={t.href} x={t.x} y={t.y} width={t.size + 0.5} height={t.size + 0.5} />)}
        {layer && (() => {
          const [x1, y1] = xy({ lat: layer.bounds.north, lon: layer.bounds.west });
          const [x2, y2] = xy({ lat: layer.bounds.south, lon: layer.bounds.east });
          return <image href={layer.image_url} x={x1} y={y1} width={x2 - x1} height={y2 - y1} preserveAspectRatio="none" />;
        })()}
        {!provider && !layer && Array.from({ length: Math.ceil(W / grid) + 1 }, (_, i) => (
          <line key={`gx${i}`} x1={i * grid - (left % grid)} y1={0} x2={i * grid - (left % grid)} y2={H} className="map-grid" />))}
        {!provider && !layer && Array.from({ length: Math.ceil(H / grid) + 1 }, (_, i) => (
          <line key={`gy${i}`} x1={0} y1={i * grid - (top % grid)} x2={W} y2={i * grid - (top % grid)} className="map-grid" />))}

        {items.map((o) => {
          const pts = routePoints(o).map(xy);
          if (pts.length < 2) return null;
          const sel = o.id === selected;
          return (
            <g key={`r-${o.id}`} className={`om-route ${sel ? "sel" : ""} ${o.priority_level === "highest" ? "highest" : ""}`}>
              <polyline points={pts.map(([x, y]) => `${x},${y}`).join(" ")} />
              {o.trail.length > 1 && <polyline className="om-trail" points={o.trail.map(([la, lo]) => xy({ lat: la, lon: lo }).join(",")).join(" ")} />}
              {o.route?.stops.map((s, i) => { const [x, y] = xy(s); return <circle key={i} cx={x} cy={y} r={sel ? 5 : 3.5} className="om-stop" />; })}
            </g>
          );
        })}
        {items.map((o) => {
          if (!o.route) return null;
          const [x, y] = xy(o.route.destination);
          return (
            <g key={`d-${o.id}`} className={`om-marker om-dest ${o.id === selected ? "sel" : ""}`} transform={`translate(${x} ${y})`}
               role="button" tabIndex={0} aria-label={`Точка доставки заказа №${o.number}`}
               onClick={() => onPick(o.id, "dest")} onKeyDown={(e) => e.key === "Enter" && onPick(o.id, "dest")}>
              <path d="M0,0 C-9,-12 -9,-24 0,-24 C9,-24 9,-12 0,0 Z" />
              <text y={-12} textAnchor="middle">{o.number}</text>
            </g>
          );
        })}
        {items.map((o) => {
          const at = droneAt(o);
          if (!at || !o.drone) return null;
          const [x, y] = xy(at);
          const live = !!o.position;
          return (
            <g key={`p-${o.id}`} className={`om-marker om-drone ${live ? "" : "stale"} ${o.id === selected ? "sel" : ""}`}
               transform={`translate(${x} ${y})`} role="button" tabIndex={0}
               aria-label={`БПЛА ${o.drone.registration_number}, заказ №${o.number}`}
               onClick={() => onPick(o.id, "drone")} onKeyDown={(e) => e.key === "Enter" && onPick(o.id, "drone")}>
              <g transform={`rotate(${o.position?.heading_deg ?? 0})`}>
                <path d="M0,-13 L3,-4 L12,1 L12,4 L3,2 L2,9 L5,12 L5,13 L0,12 L-5,13 L-5,12 L-2,9 L-3,2 L-12,4 L-12,1 L-3,-4 Z" />
              </g>
              <text x={14} y={4} className="om-label">{o.drone.registration_number}</text>
            </g>
          );
        })}
      </svg>
      <div className="om-tools">
        {toolbar}
        <button type="button" className="btn small" onClick={() => zoom(1)} aria-label="Приблизить">+</button>
        <button type="button" className="btn small" onClick={() => zoom(-1)} aria-label="Отдалить">−</button>
        <button type="button" className="btn small" onClick={() => fit(all)}>Все заказы</button>
      </div>
      {p && popupAt && (
        <div className="om-popup card" role="dialog" aria-label={`Заказ №${p.number}`}
             style={{ left: Math.min(Math.max(8, popupAt[0] + 14), W - 300), top: Math.min(Math.max(8, popupAt[1] - 20), H - 260) }}>
          <div className="row between"><strong>Заказ №{p.number}</strong><Badge kind="order" value={p.status} /></div>
          <dl className="kv compact">
            <div><dt>Оператор</dt><dd>{p.operator?.name
              ?? (p.offered_operator ? `предложен: ${p.offered_operator.name}, ждёт подтверждения`
                : p.status === "awaiting_operator" ? "ждёт оператора с нужной квалификацией на смене" : "не назначен")}</dd></div>
            <div><dt>Заказчик</dt><dd>{p.customer?.name ?? "—"}</dd></div>
            <div><dt>Координатор</dt><dd>{p.coordinator?.name ?? "не назначен"}</dd></div>
            <div><dt>Модель БПЛА</dt><dd>{p.drone ? `${p.drone.manufacturer} ${p.drone.model} (${p.drone.registration_number})` : "—"}</dd></div>
            <div><dt>Груз</dt><dd>{p.cargo.map((c) => `${c.name}, ${c.weight_g} г`).join("; ") || "—"}</dd></div>
            <div><dt>Грузополучатель</dt><dd>{p.consignee.name ?? "—"}{p.consignee.location ? ` — ${p.consignee.location}` : ""}</dd></div>
            {p.position && <div><dt>Телеметрия</dt><dd>{p.position.alt_m != null ? `${Math.round(p.position.alt_m)} м` : "—"}
              {p.position.speed_mps != null ? `, ${p.position.speed_mps.toFixed(1)} м/с` : ""}
              {p.position.battery_pct != null ? `, батарея ${p.position.battery_pct}%` : ""}</dd></div>}
          </dl>
          <div className="row">
            <button type="button" className="btn primary small" onClick={() => onOpenOrder(p.id)}>Открыть заказ</button>
            <button type="button" className="btn small" onClick={onClosePopup}>Закрыть</button>
          </div>
        </div>
      )}
      {provider && <div className="om-attrib small">{provider.attribution}</div>}
     </div>
      <div className="om-sources" role="group" aria-label="Карта">
        {sources.map((s) => (
          <button key={s.id} type="button" className={`btn small ${s.id === effective ? "primary" : ""}`} aria-pressed={s.id === effective}
                  onClick={() => { setSource(s.id); savePref(s.id); }}>{s.name}</button>
        ))}
      </div>
    </div>
  );
}
