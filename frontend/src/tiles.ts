import { api } from "./api";
import type { Point } from "./types";

// Онлайн-карты (плиточные сервисы, подключённые администратором по API-ключу). Плитки браузер
// получает только с сервера платформы: /api/v1/map-providers/<id>/tiles/z/x/y.

export interface MapProviderInfo {
  id: string;
  name: string;
  preset: string;
  projection: "3857" | "3395";      // Web Mercator (большинство сервисов) или EPSG:3395 (Яндекс)
  min_zoom: number;
  max_zoom: number;
  attribution: string;
  tile_url: string;                  // шаблон с {z}/{x}/{y}
}

let cache: Promise<MapProviderInfo[]> | null = null;
const listeners = new Set<() => void>();

export function loadProviders(): Promise<MapProviderInfo[]> {
  if (!cache) {
    cache = api.get<{ items: MapProviderInfo[] }>("/api/v1/map-providers").then((r) => r.items, () => {
      cache = null;
      return [];
    });
  }
  return cache;
}

export function invalidateProviders(): void {
  cache = null;
  listeners.forEach((f) => f());
}

export function onProvidersChanged(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export const TILE = 256;
const E = 0.0818191908426;           // эксцентриситет эллипсоида WGS 84 (для EPSG:3395)
const MAX_LAT = 85.0511;

/** Мировые пиксельные координаты точки на масштабе z. */
export function project(p: Point, z: number, projection: "3857" | "3395" = "3857"): { x: number; y: number } {
  const size = TILE * 2 ** z;
  const lat = Math.max(-MAX_LAT, Math.min(MAX_LAT, p.lat));
  const phi = (lat * Math.PI) / 180;
  let merc = Math.log(Math.tan(Math.PI / 4 + phi / 2));
  if (projection === "3395") {
    const es = E * Math.sin(phi);
    merc -= (E / 2) * Math.log((1 + es) / (1 - es));
  }
  return { x: ((p.lon + 180) / 360) * size, y: ((1 - merc / Math.PI) / 2) * size };
}

/** Обратное преобразование мировых пиксельных координат в широту и долготу. */
export function unproject(x: number, y: number, z: number, projection: "3857" | "3395" = "3857"): Point {
  const size = TILE * 2 ** z;
  const lon = (x / size) * 360 - 180;
  const merc = Math.PI * (1 - (2 * y) / size);
  let phi = 2 * Math.atan(Math.exp(merc)) - Math.PI / 2;
  if (projection === "3395") {
    // итерация для эллипсоида: φ = π/2 − 2·atan(t·((1 − e·sinφ)/(1 + e·sinφ))^(e/2)), t = e^(−merc)
    const t = Math.exp(-merc);
    for (let i = 0; i < 10; i++) {
      const es = E * Math.sin(phi);
      const next = Math.PI / 2 - 2 * Math.atan(t * ((1 - es) / (1 + es)) ** (E / 2));
      if (Math.abs(next - phi) < 1e-12) {
        phi = next;
        break;
      }
      phi = next;
    }
  }
  return { lat: (phi * 180) / Math.PI, lon };
}

/** Вид карты: центр в мировых пикселях масштаба z (дробного — плитки растягиваются). */
export interface View {
  z: number;
  cx: number;
  cy: number;
}

/** Масштаб и центр, при которых все точки помещаются в W×H с отступом pad. */
export function fitView(points: Point[], W: number, H: number, pad: number, projection: "3857" | "3395",
                        minZ: number, maxZ: number, singleZoom = 15): View {
  if (points.length === 0) {
    const z0 = Math.min(maxZ, 10);
    const c = project({ lat: 55.75, lon: 37.62 }, z0, projection);
    return { z: z0, cx: c.x, cy: c.y };
  }
  const at0 = points.map((p) => project(p, 0, projection));
  const xs = at0.map((p) => p.x), ys = at0.map((p) => p.y);
  const spanX = Math.max(...xs) - Math.min(...xs), spanY = Math.max(...ys) - Math.min(...ys);
  let z = singleZoom;
  if (spanX > 0 || spanY > 0) {
    const zx = spanX > 0 ? Math.log2((W - 2 * pad) / spanX) : Infinity;
    const zy = spanY > 0 ? Math.log2((H - 2 * pad) / spanY) : Infinity;
    z = Math.min(zx, zy);
  }
  z = Math.max(minZ, Math.min(maxZ, z));
  const k = 2 ** z;
  return { z, cx: ((Math.min(...xs) + Math.max(...xs)) / 2) * k, cy: ((Math.min(...ys) + Math.max(...ys)) / 2) * k };
}

export interface TileRef { key: string; href: string; x: number; y: number; size: number }

/** Плитки, покрывающие окно W×H для вида view; координаты — в системе окна. */
export function tilesFor(view: View, W: number, H: number, provider: MapProviderInfo): TileRef[] {
  const zi = Math.max(provider.min_zoom, Math.min(provider.max_zoom, Math.floor(view.z + 1e-9)));
  const scale = 2 ** (view.z - zi);              // растяжение плиток целого масштаба до дробного
  const size = TILE * scale;
  const left = view.cx - W / 2, top = view.cy - H / 2;
  const n = 2 ** zi;
  const out: TileRef[] = [];
  const x0 = Math.floor(left / size), x1 = Math.floor((left + W) / size);
  const y0 = Math.max(0, Math.floor(top / size)), y1 = Math.min(n - 1, Math.floor((top + H) / size));
  for (let ty = y0; ty <= y1; ty++) {
    for (let tx = x0; tx <= x1; tx++) {
      const wx = ((tx % n) + n) % n;              // по долготе карта повторяется
      out.push({
        key: `${zi}/${tx}/${ty}`, size,
        href: provider.tile_url.replace("{z}", String(zi)).replace("{x}", String(wx)).replace("{y}", String(ty)),
        x: tx * size - left, y: ty * size - top,
      });
    }
  }
  return out;
}

// Выбор карты: id онлайн-карты, «local» — загруженная карта района, «scheme» — схема без подложки.
export const LOCAL = "local";
export const SCHEME = "scheme";

const VIEW_KEY = "fpv.routeMap.";

/** Карта, которую пользователь выбрал для просмотра этого заказа (только в его браузере). */
export function readOrderMapChoice(orderId?: string): string | null {
  if (!orderId) return null;
  try {
    return localStorage.getItem(VIEW_KEY + orderId);
  } catch {
    return null;
  }
}

export function saveOrderMapChoice(orderId: string | undefined, choice: string): void {
  if (!orderId) return;
  try {
    localStorage.setItem(VIEW_KEY + orderId, choice);
  } catch {
    /* выбор действует до перезагрузки страницы */
  }
}
