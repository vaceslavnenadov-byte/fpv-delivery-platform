import { api } from "./api";
import type { MapLayer, Point } from "./types";

// Список карт загружается один раз на страницу и обновляется после изменений администратором.
let cache: Promise<MapLayer[]> | null = null;
const listeners = new Set<() => void>();

export function loadMaps(): Promise<MapLayer[]> {
  if (!cache) {
    cache = api.get<{ items: MapLayer[] }>("/api/v1/maps").then((r) => r.items, () => {
      cache = null;
      return [];
    });
  }
  return cache;
}

export function invalidateMaps(): void {
  cache = null;
  listeners.forEach((f) => f());
}

export function onMapsChanged(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

const inside = (m: MapLayer, p: Point) =>
  p.lat >= m.bounds.south && p.lat <= m.bounds.north && p.lon >= m.bounds.west && p.lon <= m.bounds.east;

/** Карта, покрывающая больше всего точек маршрута; при равенстве — самая подробная (меньшая площадь). */
export function pickMap(layers: MapLayer[], points: Point[]): MapLayer | null {
  let best: MapLayer | null = null;
  let bestScore = -1;
  let bestArea = Infinity;
  for (const m of layers) {
    const score = points.filter((p) => inside(m, p)).length;
    const area = (m.bounds.north - m.bounds.south) * (m.bounds.east - m.bounds.west);
    if (score > 0 && (score > bestScore || (score === bestScore && area < bestArea))) {
      best = m;
      bestScore = score;
      bestArea = area;
    }
  }
  return best;
}
