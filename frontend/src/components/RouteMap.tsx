import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { loadMaps, onMapsChanged, pickMap } from "../maps";
import { LOCAL, SCHEME, fitView, loadProviders, onProvidersChanged, project, readOrderMapChoice, saveOrderMapChoice,
         tilesFor, type MapProviderInfo } from "../tiles";
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

function useProviders(): MapProviderInfo[] {
  const { can } = useAuth();
  const allowed = can("map.read");
  const [items, setItems] = useState<MapProviderInfo[]>([]);
  useEffect(() => {
    if (!allowed) return;
    let alive = true;
    const load = () => loadProviders().then((l) => alive && setItems(l));
    load();
    const off = onProvidersChanged(load);
    return () => {
      alive = false;
      off();
    };
  }, [allowed]);
  return items;
}

interface Props {
  route: RouteInfo;
  track?: Telemetry[];          // передаётся только при наличии прав на телеметрию
  status?: string;
  height?: number;
  compact?: boolean;            // без текстовой подписи (монитор полёта)
  passed?: number;              // число пройденных контрольных точек
  providerId?: string | null;   // онлайн-карта, выбранная заказчиком, — показывается по умолчанию
  orderId?: string;             // для запоминания карты, которую выбрал этот пользователь
}

/** Карта маршрута (SVG). Подложка — карта, загруженная администратором платформы
 *  (внешние картографические сервисы не используются — работает в изолированной сети ЦОД);
 *  без подходящей карты показывается схема с сеткой. Позиция дрона показывается только при
 *  наличии достоверных данных (GNSS fix) и соответствующих прав. */
export function RouteMap({ route, track = [], status, height = 280, compact = false, passed = 0, providerId, orderId }: Props) {
  const layers = useBasemaps();
  const providers = useProviders();
  // Какую карту показать: выбранную этим пользователем для заказа → карту заказчика →
  // загруженную карту района (если не отключена) → схему.
  const [picked, setPicked] = useState<string | null>(() => readOrderMapChoice(orderId));
  const pick = (v: string) => {
    setPicked(v);
    saveOrderMapChoice(orderId, v);
    if (v === LOCAL || v === SCHEME) {
      try {
        localStorage.setItem(BASEMAP_PREF, v === LOCAL ? "on" : "off");
      } catch {
        /* настройка не сохраняется — не критично */
      }
    }
  };
  const fixes = track.filter((t) => t.lat != null && t.lon != null);
  // С базой парка: А — база, Б — забор груза (origin), В — получатель (destination); без базы — А→Б.
  const hasBase = !!route.base;
  const pickupLabel = hasBase ? "Б" : "А";
  const destLabel = hasBase ? "В" : "Б";
  const cps = route.checkpoints.filter((c) => c.kind !== "pickup" && c.kind !== "stop");
  const stops = route.stops ?? [];
  // Линия маршрута: [А →] контрольные точки (в т.ч. Б) → получатель.
  const line = [route.base ?? route.origin, ...route.checkpoints, route.destination];
  const pts = [...(route.base ? [route.base] : []), route.origin, route.destination, ...route.checkpoints,
               ...fixes.map((t) => ({ lat: t.lat!, lon: t.lon! }))];
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
  let x = (lon: number) => W / 2 + (lon - cx) * k * scale;
  let y = (lat: number) => H / 2 - (lat - cy) * scale;
  const last = fixes[fixes.length - 1];
  const fresh = last && Date.now() - new Date(last.recorded_at).getTime() < 30000;
  const gridStep = 50;
  const basemap = pickMap(layers, [...(route.base ? [route.base] : []), route.origin, route.destination, ...route.checkpoints]);
  const customerProvider = providers.find((p) => p.id === providerId) ?? null;
  const fallback = customerProvider ? customerProvider.id : basemap && readPref() ? LOCAL : SCHEME;
  const choice = picked && (picked === SCHEME || (picked === LOCAL && basemap) || providers.some((p) => p.id === picked))
    ? picked : fallback;
  const provider = providers.find((p) => p.id === choice) ?? null;
  const base = choice === LOCAL ? basemap : null;
  // Онлайн-карта: проекция сервиса, масштаб подбирается так, чтобы поместился весь маршрут.
  const view = provider ? fitView(pts, W, H, pad, provider.projection, provider.min_zoom, provider.max_zoom) : null;
  if (provider && view) {
    const left = view.cx - W / 2, top = view.cy - H / 2;
    x = (lon: number) => project({ lat: 0, lon }, view.z, provider.projection).x - left;
    y = (lat: number) => project({ lat, lon: 0 }, view.z, provider.projection).y - top;
  }

  return (
    <figure className="map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Карта маршрута" className={base || provider ? "with-base" : ""}>
        <rect width={W} height={H} className="map-bg" />
        {provider && view && tilesFor(view, W, H, provider).map((t) => (
          <image key={t.key} href={t.href} x={t.x} y={t.y} width={t.size + 0.5} height={t.size + 0.5} preserveAspectRatio="none" />
        ))}
        {base && (
          <image href={base.image_url} x={x(base.bounds.west)} y={y(base.bounds.north)}
                 width={x(base.bounds.east) - x(base.bounds.west)} height={y(base.bounds.south) - y(base.bounds.north)}
                 preserveAspectRatio="none" className="map-base" />
        )}
        {!base && !provider && Array.from({ length: Math.ceil(W / gridStep) }, (_, i) => (
          <line key={`v${i}`} x1={i * gridStep} y1={0} x2={i * gridStep} y2={H} className="map-grid" />
        ))}
        {!base && !provider && Array.from({ length: Math.ceil(H / gridStep) }, (_, i) => (
          <line key={`h${i}`} x1={0} y1={i * gridStep} x2={W} y2={i * gridStep} className="map-grid" />
        ))}
        <polyline points={line.map((p) => `${x(p.lon)},${y(p.lat)}`).join(" ")}
                  className={`map-route ${route.approval_status === "approved" ? "approved" : ""}`} />
        {cps.map((c) => (
          <g key={c.name}>
            <circle cx={x(c.lon)} cy={y(c.lat)} r={4} className={`map-cp ${route.checkpoints.indexOf(c) < passed ? "passed" : ""}`} />
            <text x={x(c.lon) + 6} y={y(c.lat) - 6} className="map-label small">{c.name}</text>
          </g>
        ))}
        {fixes.length > 1 && (
          <polyline points={fixes.map((t) => `${x(t.lon!)},${y(t.lat!)}`).join(" ")} className="map-track" />
        )}
        {route.base && (
          <g>
            <circle cx={x(route.base.lon)} cy={y(route.base.lat)} r={9} className="map-base-pin" />
            <text x={x(route.base.lon)} y={y(route.base.lat) + 4} className="map-pin">А</text>
          </g>
        )}
        <g>
          <circle cx={x(route.origin.lon)} cy={y(route.origin.lat)} r={9} className={hasBase ? "map-pickup" : "map-a"} />
          <text x={x(route.origin.lon)} y={y(route.origin.lat) + 4} className="map-pin">{pickupLabel}</text>
        </g>
        {stops.map((st, i) => (
          <g key={`stop-${i}`}>
            <title>{`${i + 1} — ${st.address}${st.note ? ` (${st.note})` : ""}`}</title>
            <circle cx={x(st.lon)} cy={y(st.lat)} r={9} className="map-stop" />
            <text x={x(st.lon)} y={y(st.lat) + 4} className="map-pin">{i + 1}</text>
          </g>
        ))}
        <g>
          <circle cx={x(route.destination.lon)} cy={y(route.destination.lat)} r={9} className="map-b" />
          <text x={x(route.destination.lon)} y={y(route.destination.lat) + 4} className="map-pin">{destLabel}</text>
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
        {!compact && hasBase && <>А — база парка, Б — забор груза, {stops.length > 0 && <>1–{stops.length} — промежуточные точки, </>}В — получатель. </>}
        {!compact && <>Маршрут {(route.distance_m / 1000).toFixed(2)} км{route.return_m != null && `, возврат на базу ${(route.return_m / 1000).toFixed(2)} км`}, высота до {route.max_altitude_m} м.
        {status && <> Статус: {status}.</>} {track.length === 0 && "Позиция дрона не отображается (нет данных или прав доступа)."}</>}
        {(basemap || providers.length > 0) && (
          <label className="map-toggle">Карта:{" "}
            <select value={choice} onChange={(e) => pick(e.target.value)} aria-label="Карта маршрута">
              {providers.map((p) => (
                <option key={p.id} value={p.id}>{p.name}{p.id === providerId ? " — выбрана заказчиком" : ""}</option>
              ))}
              {basemap && <option value={LOCAL}>Карта района: {basemap.name}</option>}
              <option value={SCHEME}>Схема без подложки</option>
            </select>
          </label>
        )}
        {provider?.attribution && <span className="map-attribution-text">{provider.attribution}</span>}
      </figcaption>
    </figure>
  );
}
