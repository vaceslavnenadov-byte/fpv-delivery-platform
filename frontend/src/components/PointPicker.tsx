import { useEffect, useState, type MouseEvent } from "react";
import { loadMaps, onMapsChanged, pickMap } from "../maps";
import type { MapLayer, Point } from "../types";

/** Выбор одной точки (база парка) щелчком по карте района, загруженной администратором.
 *  Точку можно уточнить полями координат рядом — значения синхронизированы. */
export function PointPicker({ value, onChange, label = "А", height = 320 }: {
  value: Point; onChange: (p: Point) => void; label?: string; height?: number;
}) {
  const [layers, setLayers] = useState<MapLayer[]>([]);
  const [mapId, setMapId] = useState("");
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

  const auto = pickMap(layers, [value]) ?? layers[0] ?? null;
  const map = layers.find((m) => m.id === mapId) ?? auto;
  if (!map) {
    return <p className="muted small">Карт района нет — укажите координаты базы вручную (карту загружает администратор:
      «Карты»).</p>;
  }
  const b = map.bounds;
  // Пропорции изображения — по реальным размерам района (долгота сжимается к полюсам).
  const k = Math.cos((((b.north + b.south) / 2) * Math.PI) / 180);
  const W = 600;
  const H = Math.max(160, Math.min(height, Math.round(W * (b.north - b.south) / ((b.east - b.west) * k))));
  const x = (lon: number) => ((lon - b.west) / (b.east - b.west)) * W;
  const y = (lat: number) => ((b.north - lat) / (b.north - b.south)) * H;
  const inside = value.lat <= b.north && value.lat >= b.south && value.lon >= b.west && value.lon <= b.east;

  function pick(e: MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    const fy = (e.clientY - r.top) / r.height;
    const lat = b.north - fy * (b.north - b.south);
    const lon = b.west + fx * (b.east - b.west);
    onChange({ ...value, lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lon * 1e6) / 1e6 });
  }

  return (
    <figure className="map point-picker">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Карта: щёлкните, чтобы поставить точку ${label}`}
           onClick={pick} className="with-base">
        <image href={map.image_url} x={0} y={0} width={W} height={H} preserveAspectRatio="none" className="map-base" />
        {inside && (
          <g>
            <circle cx={x(value.lon)} cy={y(value.lat)} r={11} className="map-base-pin" />
            <text x={x(value.lon)} y={y(value.lat) + 4} className="map-pin">{label}</text>
          </g>
        )}
      </svg>
      <figcaption className="row between">
        <span className="small muted">Щёлкните по карте, чтобы поставить точку {label}
          {!inside && " (сейчас точка вне этой карты)"}.</span>
        {layers.length > 1 && (
          <select value={map.id} onChange={(e) => setMapId(e.target.value)} aria-label="Карта района">
            {layers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}
      </figcaption>
    </figure>
  );
}
