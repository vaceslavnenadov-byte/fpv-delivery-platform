import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { loadMaps, onMapsChanged, pickMap } from "../maps";
import { LOCAL, fitView, loadProviders, onProvidersChanged, project, tilesFor, unproject,
         type MapProviderInfo, type View } from "../tiles";
import { inArea, loadServiceArea, onServiceAreaChanged, type Area } from "../serviceArea";
import type { MapLayer, Point } from "../types";

/** Точка на карте-редакторе. locked — показывается, но не перемещается (например, база парка
 *  в заказе: её меняет техник). */
export interface MapPin {
  key: string;
  label: string;          // «А», «Б», «В»
  title: string;          // подсказка: «Б — где забрать груз»
  point: Point;
  className: string;      // цвет метки: map-base-pin / map-pickup / map-b
  locked?: boolean;
}

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
const listRu = (xs: string[]) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} и ${xs[xs.length - 1]}` : xs.join(""));
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const W = 600;

interface Frame {
  H: number;
  x: (p: Point) => number;
  y: (p: Point) => number;
  at: (vx: number, vy: number) => Point;        // точка карты по координатам окна (viewBox)
  inside: (p: Point) => boolean;
  background: ReactNode;
}

/** Карта с метками, которые перетаскиваются мышью или пальцем. Щелчок по пустому месту ставит
 *  выбранную точку (active), метку в фокусе можно сдвигать стрелками (Shift — крупнее).
 *  Подложка — загруженная карта района или онлайн-карта (её можно двигать и масштабировать).
 *  Маршрут между метками рисуется пунктиром в порядке массива pins. */
export function MapPointsEditor({ pins, onChange, active, onActiveChange, height = 360, emptyHint, source, onSourceChange,
                                 showSourceSelect = true, area, showServiceArea = true, route = true, fitKey }: {
  pins: MapPin[];
  onChange: (key: string, p: Point) => void;
  active?: string;
  onActiveChange?: (key: string) => void;
  height?: number;
  emptyHint?: string;
  source?: string;                                 // id онлайн-карты или «local»
  onSourceChange?: (s: string) => void;
  showSourceSelect?: boolean;                      // false — карту выбирают отдельным полем формы
  area?: Area | null;                              // прямоугольник для показа (по умолчанию — зона обслуживания)
  showServiceArea?: boolean;                       // показывать контур зоны обслуживания
  route?: boolean;                                 // линия между метками (маршрут)
  fitKey?: string | number;                        // при изменении — показать все метки заново
}) {
  const [layers, setLayers] = useState<MapLayer[]>([]);
  const [providers, setProviders] = useState<MapProviderInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [mapId, setMapId] = useState("");
  const [ownSource, setOwnSource] = useState("");
  const [view, setView] = useState<View | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const pan = useRef<{ px: number; py: number; cx: number; cy: number } | null>(null);
  const moved = useRef(false);
  const svg = useRef<SVGSVGElement>(null);
  const [zone, setZone] = useState<Area | null>(null);
  useEffect(() => {
    if (!showServiceArea) return;
    let alive = true;
    const load = () => loadServiceArea().then((a) => alive && setZone(a));
    load();
    const off = onServiceAreaChanged(load);
    return () => {
      alive = false;
      off();
    };
  }, [showServiceArea]);
  const rect = area ?? (showServiceArea ? zone : null);

  useEffect(() => {
    let alive = true;
    const load = () => Promise.all([loadMaps(), loadProviders()]).then(([l, p]) => {
      if (!alive) return;
      setLayers(l.filter((m) => m.active));
      setProviders(p);
      setLoaded(true);
    });
    load();
    const off1 = onMapsChanged(load);
    const off2 = onProvidersChanged(load);
    return () => {
      alive = false;
      off1();
      off2();
    };
  }, []);

  // Подложка по умолчанию: загруженная карта района, если есть, иначе первая онлайн-карта.
  const chosen = source ?? ownSource;
  // Без явного выбора: карта района, если все точки на ней помещаются, иначе — онлайн-карта.
  const fitsLocal = layers.length > 0 && (!providers.length || pins.every((pin) => layers.some((m) =>
    pin.point.lat <= m.bounds.north && pin.point.lat >= m.bounds.south && pin.point.lon >= m.bounds.west && pin.point.lon <= m.bounds.east)));
  const provider = providers.find((p) => p.id === chosen)
    ?? (chosen === LOCAL && layers.length ? null : fitsLocal ? null : providers[0] ?? null);
  const effective = provider ? provider.id : layers.length ? LOCAL : "";
  useEffect(() => {
    if (loaded && effective && effective !== chosen) (onSourceChange ?? setOwnSource)(effective);
  }, [loaded, effective, chosen, onSourceChange]);

  // Онлайн-карта: при выборе — показать все точки.
  const fitAll = () => provider && setView(fitView(pins.map((p) => p.point), W, height, 50, provider.projection,
    provider.min_zoom, provider.max_zoom));
  useEffect(() => {
    if (provider) fitAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider?.id, fitKey]);

  // Колесо мыши масштабирует онлайн-карту относительно указателя (обработчик не пассивный,
  // чтобы страница при этом не прокручивалась).
  useEffect(() => {
    const el = svg.current;
    if (!el || !provider) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 0.5 : -0.5, ((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * height);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // Карта района выбирается один раз по точкам и не «прыгает» во время перетаскивания.
  const [autoId, setAutoId] = useState("");
  useEffect(() => {
    if (!autoId && layers.length) setAutoId((pickMap(layers, pins.map((p) => p.point)) ?? layers[0]).id);
  }, [layers, autoId, pins]);
  const layer = layers.find((m) => m.id === (mapId || autoId)) ?? null;

  function zoomAt(dz: number, vx: number, vy: number) {
    setView((v) => {
      if (!v || !provider) return v;
      const z = clamp(v.z + dz, provider.min_zoom, provider.max_zoom);
      const k = 2 ** (z - v.z);
      const wx = v.cx - W / 2 + vx, wy = v.cy - height / 2 + vy;
      return { z, cx: wx * k - vx + W / 2, cy: wy * k - vy + height / 2 };
    });
  }

  let frame: Frame | null = null;
  if (provider && view) {
    const H = height;
    const left = view.cx - W / 2, top = view.cy - H / 2;
    const proj = provider.projection;
    const x = (p: Point) => project(p, view.z, proj).x - left;
    const y = (p: Point) => project(p, view.z, proj).y - top;
    frame = {
      H, x, y,
      at: (vx, vy) => { const p = unproject(left + vx, top + vy, view.z, proj); return { lat: round6(p.lat), lon: round6(p.lon) }; },
      inside: (p) => { const px = x(p), py = y(p); return px >= -20 && px <= W + 20 && py >= -20 && py <= H + 20; },
      background: (
        <g className="map-tiles">
          {tilesFor(view, W, H, provider).map((t) => (
            <image key={t.key} href={t.href} x={t.x} y={t.y} width={t.size + 0.5} height={t.size + 0.5} preserveAspectRatio="none" />
          ))}
          {provider.attribution && <text x={W - 6} y={H - 6} className="map-attribution">{provider.attribution}</text>}
        </g>
      ),
    };
  } else if (!provider && layer) {
    const b = layer.bounds;
    const k = Math.cos((((b.north + b.south) / 2) * Math.PI) / 180);
    const H = Math.max(180, Math.min(height, Math.round(W * (b.north - b.south) / ((b.east - b.west) * k))));
    frame = {
      H,
      x: (p) => ((p.lon - b.west) / (b.east - b.west)) * W,
      y: (p) => ((b.north - p.lat) / (b.north - b.south)) * H,
      at: (vx, vy) => ({ lat: round6(b.north - clamp(vy / H, 0, 1) * (b.north - b.south)),
                         lon: round6(b.west + clamp(vx / W, 0, 1) * (b.east - b.west)) }),
      inside: (p) => p.lat <= b.north && p.lat >= b.south && p.lon >= b.west && p.lon <= b.east,
      background: <image href={layer.image_url} x={0} y={0} width={W} height={H} preserveAspectRatio="none" className="map-base" />,
    };
  }

  if (!loaded) return null;
  if (!frame) {
    if (provider) return null;                     // вид онлайн-карты ещё рассчитывается
    return <p className="muted small">{emptyHint ?? "Карт нет — укажите координаты вручную (карты подключает администратор: «Карты»)."}</p>;
  }
  const f = frame;

  function viewBoxPoint(clientX: number, clientY: number): [number, number] {
    const r = svg.current!.getBoundingClientRect();
    return [((clientX - r.left) / r.width) * W, ((clientY - r.top) / r.height) * f.H];
  }
  function startDrag(e: PointerEvent<SVGGElement>, pin: MapPin) {
    if (pin.locked) return;
    e.stopPropagation();
    e.preventDefault();
    svg.current?.setPointerCapture(e.pointerId);
    moved.current = false;
    setDragging(pin.key);
    onActiveChange?.(pin.key);
  }
  function down(e: PointerEvent<SVGSVGElement>) {
    moved.current = false;
    if (!provider || !view || e.button !== 0) return;
    const [px, py] = viewBoxPoint(e.clientX, e.clientY);
    pan.current = { px, py, cx: view.cx, cy: view.cy };
    svg.current?.setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent<SVGSVGElement>) {
    const [vx, vy] = viewBoxPoint(e.clientX, e.clientY);
    if (dragging) {
      moved.current = true;
      onChange(dragging, f.at(clamp(vx, 0, W), clamp(vy, 0, f.H)));
    } else if (pan.current && view) {
      const s = pan.current;
      if (!moved.current && Math.hypot(vx - s.px, vy - s.py) < 4) return;   // ещё щелчок, не сдвиг
      moved.current = true;
      setView({ ...view, cx: s.cx - (vx - s.px), cy: s.cy - (vy - s.py) });
    }
  }
  function up(e: PointerEvent<SVGSVGElement>) {
    if (svg.current?.hasPointerCapture(e.pointerId)) svg.current.releasePointerCapture(e.pointerId);
    pan.current = null;
    setDragging(null);
  }
  function place(e: MouseEvent<SVGSVGElement>) {
    if (moved.current) {                 // щелчок, завершивший перетаскивание или сдвиг карты, точку не ставит
      moved.current = false;
      return;
    }
    const target = pins.find((p) => p.key === active && !p.locked) ?? pins.find((p) => !p.locked);
    const [vx, vy] = viewBoxPoint(e.clientX, e.clientY);
    if (target) onChange(target.key, f.at(vx, vy));
  }
  function nudge(e: KeyboardEvent<SVGGElement>, pin: MapPin) {
    const d: Record<string, [number, number]> = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
    const dir = d[e.key];
    if (!dir || pin.locked) return;
    e.preventDefault();
    const px = e.shiftKey ? 20 : 2;                  // шаг в точках изображения карты
    onChange(pin.key, f.at(clamp(f.x(pin.point) + dir[0] * px, 0, W), clamp(f.y(pin.point) + dir[1] * px, 0, f.H)));
  }

  const visible = pins.filter((p) => f.inside(p.point));
  const outside = pins.filter((p) => !f.inside(p.point));
  const movable = pins.filter((p) => !p.locked);
  const setSource = onSourceChange ?? setOwnSource;
  const sources = [...(layers.length ? [{ id: LOCAL, name: "Карта района (загруженная)" }] : []), ...providers];
  return (
    <figure className="map point-picker">
      {movable.length > 1 && onActiveChange && (
        <div className="pin-tabs" role="group" aria-label="Какую точку ставить щелчком">
          {movable.map((p) => (
            <button key={p.key} type="button" aria-pressed={active === p.key} onClick={() => onActiveChange(p.key)}>
              <span className={`pin-dot ${p.className}`}>{p.label}</span> {p.title}
            </button>
          ))}
        </div>
      )}
      <div className="map-frame">
        <svg ref={svg} viewBox={`0 0 ${W} ${f.H}`} role="application"
             className={`with-base ${dragging ? "dragging" : ""} ${provider ? "pannable" : ""}`}
             aria-label="Карта: перетащите метки или щёлкните, чтобы поставить выбранную точку"
             onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onClick={place}>
          {f.background}
          {rect && (() => {
            const x1 = f.x({ lat: rect.north, lon: rect.west }), y1 = f.y({ lat: rect.north, lon: rect.west });
            const x2 = f.x({ lat: rect.south, lon: rect.east }), y2 = f.y({ lat: rect.south, lon: rect.east });
            return (
              <g className={`zone ${area ? "editing" : ""}`} pointerEvents="none">
                <rect x={Math.min(x1, x2)} y={Math.min(y1, y2)} width={Math.abs(x2 - x1)} height={Math.abs(y2 - y1)} className="zone-outline" />
                <text x={Math.min(x1, x2) + 22} y={Math.min(y1, y2) + 30} className="zone-label">Зона обслуживания</text>
              </g>
            );
          })()}
          {route && visible.length > 1 && (
            <polyline className="map-route" points={pins.map((p) => `${f.x(p.point)},${f.y(p.point)}`).join(" ")} />
          )}
          {visible.map((p) => (
            <g key={p.key} className={`pin ${p.locked ? "locked" : "movable"} ${dragging === p.key ? "drag" : ""} ${active === p.key ? "active" : ""}`}
               transform={`translate(${f.x(p.point)} ${f.y(p.point)})`}
               tabIndex={p.locked ? -1 : 0} role={p.locked ? "img" : "slider"}
               aria-label={`${p.title}${p.locked ? " (закреплена)" : " — перетащите или двигайте стрелками"}`}
               aria-valuetext={`${p.point.lat.toFixed(5)}, ${p.point.lon.toFixed(5)}`}
               onPointerDown={(e) => startDrag(e, p)} onKeyDown={(e) => nudge(e, p)}
               onClick={(e) => e.stopPropagation()}>
              <title>{p.title}{p.locked ? " — закреплена" : " — перетащите"}</title>
              <circle r={18} className="pin-hit" />
              <circle r={12} className={p.className} />
              <text y={4} className="map-pin">{p.label}</text>
              {p.locked && <text x={12} y={-9} className="pin-lock">🔒</text>}
            </g>
          ))}
        </svg>
        {provider && (
          <div className="map-zoom">
            <button type="button" aria-label="Приблизить" onClick={() => zoomAt(1, W / 2, f.H / 2)}>+</button>
            <button type="button" aria-label="Отдалить" onClick={() => zoomAt(-1, W / 2, f.H / 2)}>−</button>
            <button type="button" aria-label="Показать все точки" title="Показать все точки" onClick={fitAll}>⤢</button>
          </div>
        )}
      </div>
      <figcaption className="row between">
        <span className="small muted">
          Перетащите {movable.length > 1 ? "метки" : "метку"} {listRu(movable.map((p) => p.label))} или щёлкните по карте
          {movable.length > 1 && onActiveChange ? " — встанет выбранная точка" : ""}.
          {provider && " Карту можно сдвигать мышью, масштаб — колесом или кнопками."}
          {pins.some((p) => p.locked) && ` ${pins.filter((p) => p.locked).map((p) => p.label).join(", ")} — закреплена.`}
          {outside.length > 0 && ` Вне видимой области: ${outside.map((p) => p.label).join(", ")}${provider ? " (⤢ — показать все)" : ""}.`}
          {showServiceArea && zone && pins.some((p) => !p.locked && !inArea(zone, p.point)) &&
            " Внимание: есть точки вне зоны обслуживания (пунктирная рамка) — такой маршрут не пройдёт проверку."}
        </span>
        <span className="row">
          {showSourceSelect && sources.length > 1 && (
            <select value={effective} onChange={(e) => setSource(e.target.value)} aria-label="Карта">
              {sources.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          )}
          {!provider && layers.length > 1 && (
            <select value={layer!.id} onChange={(e) => setMapId(e.target.value)} aria-label="Карта района">
              {layers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          )}
        </span>
      </figcaption>
    </figure>
  );
}
