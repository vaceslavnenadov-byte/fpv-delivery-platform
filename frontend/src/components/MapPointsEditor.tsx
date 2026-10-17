import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { loadMaps, onMapsChanged, pickMap } from "../maps";
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

/** Карта района с метками, которые перетаскиваются мышью или пальцем. Щелчок по пустому месту
 *  ставит выбранную точку (active). Метку в фокусе можно сдвигать стрелками (Shift — крупнее).
 *  Маршрут между метками рисуется пунктиром в порядке массива pins. */
export function MapPointsEditor({ pins, onChange, active, onActiveChange, height = 360, emptyHint }: {
  pins: MapPin[];
  onChange: (key: string, p: Point) => void;
  active?: string;
  onActiveChange?: (key: string) => void;
  height?: number;
  emptyHint?: string;
}) {
  const [layers, setLayers] = useState<MapLayer[]>([]);
  const [mapId, setMapId] = useState("");
  const [dragging, setDragging] = useState<string | null>(null);
  const moved = useRef(false);
  const svg = useRef<SVGSVGElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () => loadMaps().then((l) => alive && setLayers(l.filter((m) => m.active)));
    load();
    const off = onMapsChanged(load);
    return () => {
      alive = false;
      off();
    };
  }, []);

  // Карта выбирается один раз по точкам на момент загрузки и не «прыгает» во время перетаскивания.
  const [autoId, setAutoId] = useState("");
  useEffect(() => {
    if (!autoId && layers.length) setAutoId((pickMap(layers, pins.map((p) => p.point)) ?? layers[0]).id);
  }, [layers, autoId, pins]);
  const map = layers.find((m) => m.id === (mapId || autoId)) ?? null;

  if (!map) {
    return <p className="muted small">{emptyHint ?? "Карт района нет — укажите координаты вручную (карту загружает администратор: «Карты»)."}</p>;
  }
  const b = map.bounds;
  const k = Math.cos((((b.north + b.south) / 2) * Math.PI) / 180);
  const W = 600;
  const H = Math.max(180, Math.min(height, Math.round(W * (b.north - b.south) / ((b.east - b.west) * k))));
  const x = (lon: number) => ((lon - b.west) / (b.east - b.west)) * W;
  const y = (lat: number) => ((b.north - lat) / (b.north - b.south)) * H;
  const inside = (p: Point) => p.lat <= b.north && p.lat >= b.south && p.lon >= b.west && p.lon <= b.east;
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

  function toPoint(clientX: number, clientY: number): Point {
    const r = svg.current!.getBoundingClientRect();
    const fx = clamp((clientX - r.left) / r.width, 0, 1);
    const fy = clamp((clientY - r.top) / r.height, 0, 1);
    return { lat: round6(b.north - fy * (b.north - b.south)), lon: round6(b.west + fx * (b.east - b.west)) };
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
  function move(e: PointerEvent<SVGSVGElement>) {
    if (!dragging) return;
    moved.current = true;
    onChange(dragging, toPoint(e.clientX, e.clientY));
  }
  function endDrag(e: PointerEvent<SVGSVGElement>) {
    if (!dragging) return;
    if (svg.current?.hasPointerCapture(e.pointerId)) svg.current.releasePointerCapture(e.pointerId);
    setDragging(null);
  }
  function place(e: MouseEvent<SVGSVGElement>) {
    if (moved.current) {                 // щелчок, завершивший перетаскивание, точку не ставит
      moved.current = false;
      return;
    }
    const target = pins.find((p) => p.key === active && !p.locked) ?? pins.find((p) => !p.locked);
    if (target) onChange(target.key, toPoint(e.clientX, e.clientY));
  }
  function nudge(e: KeyboardEvent<SVGGElement>, pin: MapPin) {
    const d: Record<string, [number, number]> = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
    const dir = d[e.key];
    if (!dir || pin.locked) return;
    e.preventDefault();
    const px = e.shiftKey ? 20 : 2;                  // шаг в точках изображения карты
    onChange(pin.key, {
      lat: round6(clamp(pin.point.lat - (dir[1] * px / H) * (b.north - b.south), b.south, b.north)),
      lon: round6(clamp(pin.point.lon + (dir[0] * px / W) * (b.east - b.west), b.west, b.east)),
    });
  }

  const visible = pins.filter((p) => inside(p.point));
  const outside = pins.filter((p) => !inside(p.point));
  const movable = pins.filter((p) => !p.locked);
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
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="application" className={`with-base ${dragging ? "dragging" : ""}`}
           aria-label="Карта: перетащите метки или щёлкните, чтобы поставить выбранную точку"
           onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag} onClick={place}>
        <image href={map.image_url} x={0} y={0} width={W} height={H} preserveAspectRatio="none" className="map-base" />
        {visible.length > 1 && (
          <polyline className="map-route" points={visible.map((p) => `${x(p.point.lon)},${y(p.point.lat)}`).join(" ")} />
        )}
        {visible.map((p) => (
          <g key={p.key} className={`pin ${p.locked ? "locked" : "movable"} ${dragging === p.key ? "drag" : ""} ${active === p.key ? "active" : ""}`}
             transform={`translate(${x(p.point.lon)} ${y(p.point.lat)})`}
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
      <figcaption className="row between">
        <span className="small muted">
          Перетащите метки {movable.map((p) => p.label).join(" и ")} или щёлкните по карте
          {movable.length > 1 && onActiveChange ? " — встанет выбранная точка" : ""}.
          {pins.some((p) => p.locked) && ` ${pins.filter((p) => p.locked).map((p) => p.label).join(", ")} — закреплена.`}
          {outside.length > 0 && ` Вне этой карты: ${outside.map((p) => p.label).join(", ")}.`}
        </span>
        {layers.length > 1 && (
          <select value={map.id} onChange={(e) => setMapId(e.target.value)} aria-label="Карта района">
            {layers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}
      </figcaption>
    </figure>
  );
}
