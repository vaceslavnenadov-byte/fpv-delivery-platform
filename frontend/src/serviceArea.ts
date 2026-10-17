import { api } from "./api";

// Зоны обслуживания: прямоугольники, внутри которых должны лежать базы парков и точки маршрутов.
// Создаёт администратор («Зоны обслуживания») и назначает в них техников. Сервер отдаёт каждому
// пользователю нужное: технику — его зоны, заказчику — все активные, администратору — с техниками.

export interface Area {
  south: number;
  west: number;
  north: number;
  east: number;
}

export interface Zone extends Area {
  id: string;
  name: string;
  active: boolean;
  open_to_all?: boolean;
}

let cache: Promise<Zone[]> | null = null;
const listeners = new Set<() => void>();

export function loadZones(): Promise<Zone[]> {
  if (!cache) {
    cache = api.get<{ items: Zone[] }>("/api/v1/service-zones").then((r) => r.items, () => {
      cache = null;
      return [];
    });
  }
  return cache;
}

export function invalidateZones(): void {
  cache = null;
  listeners.forEach((f) => f());
}

export function onZonesChanged(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export const inArea = (a: Area, p: { lat: number; lon: number }) =>
  p.lat >= a.south && p.lat <= a.north && p.lon >= a.west && p.lon <= a.east;
