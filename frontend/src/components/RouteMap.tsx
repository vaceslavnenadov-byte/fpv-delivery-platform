import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { loadMaps, onMapsChanged, pickMap } from "../maps";
import type { MapLayer, RouteInfo, Telemetry } from "../types";

const BASEMAP_PREF = "fpv.basemap";

function readPref(): boolean {
  try {
    return localStorage.getItem(BASEMAP_PREF) !== "off";
  } catch {
    return true;
  }
}

function useBasemaps(): MapLayer[] {
  const { can } = useAuth();
  const allowed = can("map.read");
  const [layers, setLayers] = useState<MapLayer[]>([]);
  useEffect(() => {
    if (!allowed) return;
    let alive = true;
    const load = () => loadMaps().then((l) => alive && setLayers(l));
    load();
    const off = onMapsChanged(load);
    return () => {
      alive = false;
      off();
    };
  }, [allowed]);
  return layers;
}

interface Props {
  route: RouteInfo;
  track?: Telemetry[];          // передаётся только при наличии прав на телеметрию
  status?: string;
  height?: number;
  compact?: boolean;            // без текстовой подписи (монитор полёта)
  passed?: number;              // число пройденных контрольных точек
}

/** Карта маршрута (SVG). Подложка — карта, загруженная администратором платформы
 *  (внешние картографические сервисы не используются — работает в изолированной сети ЦОД);
 *  без подходящей карты показывается схема с сеткой. Позиция дрона показывается только при
 *  наличии достоверных данных (GNSS fix) и соответствующих прав. */
export function RouteMap({ route, track = [], status, height = 280, compact = false, passed = 0 }: Props) {
  const layers = useBasemaps();
  const [showBase, setShowBase] = useState(readPref);
  const toggleBase = (on: boolean) => {
    setShowBase(on);
    try {
      localStorage.setItem(BASEMAP_PREF, on ? "on" : "off");
    } catch {
      /* настройка не сохраняется — не критично */
    }
  };
  const fixes = track.filter((t) => t.lat != null && t.lon != null);
  const pts = [route.origin, route.destination, ...route.checkpoints, ...fixes.map((t) => ({ lat: t.lat!, lon: t.lon! }))];
  const lats = pts.map((p) => p.lat);
  const lons = pts.map((p) => p.lon);
  const latMin = Math.min(...lats), latMax = Math.max(...lats);
  const lonMin = Math.min(...lons), lonMax = Math.max(...lons);
  const k = Math.cos(((latMin + latMax) / 2) * (Math.PI / 180));
  const spanX = Math.max((lonMax - lonMin) * k, 0.002);
  const spanY = Math.max(latMax - latMin, 0.002);
  const W = 600, H = height, pad = 40;
  const scale = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const cx = (lonMin + lonMax) / 2, cy = (latMin + latMax) / 2;
  const x = (lon: number) => W / 2 + (lon - cx) * k * scale;
  const y = (lat: number) => H / 2 - (lat - cy) * scale;
  const last = fixes[fixes.length - 1];
  const fresh = last && Date.now() - new Date(last.recorded_at).getTime() < 30000;
  const gridStep = 50;
  const basemap = pickMap(layers, [route.origin, route.destination, ...route.checkpoints]);
  const base = showBase ? basemap : null;

  return (
    <figure className="map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Карта маршрута" className={base ? "with-base" : ""}>
        <rect width={W} height={H} className="map-bg" />
        {base && (
          <image href={base.image_url} x={x(base.bounds.west)} y={y(base.bounds.north)}
                 width={x(base.bounds.east) - x(base.bounds.west)} height={y(base.bounds.south) - y(base.bounds.north)}
                 preserveAspectRatio="none" className="map-base" />
        )}
        {!base && Array.from({ length: Math.ceil(W / gridStep) }, (_, i) => (
          <line key={`v${i}`} x1={i * gridStep} y1={0} x2={i * gridStep} y2={H} className="map-grid" />
        ))}
        {!base && Array.from({ length: Math.ceil(H / gridStep) }, (_, i) => (
          <line key={`h${i}`} x1={0} y1={i * gridStep} x2={W} y2={i * gridStep} className="map-grid" />
        ))}
        <line x1={x(route.origin.lon)} y1={y(route.origin.lat)} x2={x(route.destination.lon)} y2={y(route.destination.lat)}
              className={`map-route ${route.approval_status === "approved" ? "approved" : ""}`} />
        {route.checkpoints.map((c) => (
          <g key={c.name}>
            <circle cx={x(c.lon)} cy={y(c.lat)} r={4} className={`map-cp ${route.checkpoints.indexOf(c) < passed ? "passed" : ""}`} />
            <text x={x(c.lon) + 6} y={y(c.lat) - 6} className="map-label small">{c.name}</text>
          </g>
        ))}
        {fixes.length > 1 && (
          <polyline points={fixes.map((t) => `${x(t.lon!)},${y(t.lat!)}`).join(" ")} className="map-track" />
        )}
        <g>
          <circle cx={x(route.origin.lon)} cy={y(route.origin.lat)} r={9} className="map-a" />
          <text x={x(route.origin.lon)} y={y(route.origin.lat) + 4} className="map-pin">А</text>
        </g>
        <g>
          <circle cx={x(route.destination.lon)} cy={y(route.destination.lat)} r={9} className="map-b" />
          <text x={x(route.destination.lon)} y={y(route.destination.lat) + 4} className="map-pin">Б</text>
        </g>
        {last && (
          <g>
            {last.heading_deg != null ? (
              <polygon points="0,-11 8,8 0,3 -8,8" className={`map-drone ${fresh ? "" : "stale"}`}
                       transform={`translate(${x(last.lon!)} ${y(last.lat!)}) rotate(${last.heading_deg})`} />
            ) : <circle cx={x(last.lon!)} cy={y(last.lat!)} r={7} className={`map-drone ${fresh ? "" : "stale"}`} />}
            <text x={x(last.lon!) + 10} y={y(last.lat!) + 4} className="map-label">
              Дрон {fresh ? "" : "(данные устарели)"}
            </text>
          </g>
        )}
      </svg>
      <figcaption>
        {!compact && <>Маршрут {(route.distance_m / 1000).toFixed(2)} км, высота до {route.max_altitude_m} м.
        {status && <> Статус: {status}.</>} {track.length === 0 && "Позиция дрона не отображается (нет данных или прав доступа)."}</>}
        {basemap && (
          <label className="map-toggle">
            <input type="checkbox" checked={showBase} onChange={(e) => toggleBase(e.target.checked)} /> Карта: {basemap.name}
          </label>
        )}
      </figcaption>
    </figure>
  );
}
