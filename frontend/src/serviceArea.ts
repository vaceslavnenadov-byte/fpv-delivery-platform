import { api } from "./api";

// Зона обслуживания платформы: прямоугольник, внутри которого должны лежать базы парков и точки
// маршрутов. Задаёт администратор («Зона обслуживания»); показывается контуром на картах-редакторах.

export interface Area {
  south: number;
  west: number;
  north: number;
  east: number;
}

export interface ServiceAreaInfo extends Area {
  source: "admin" | "config";
  config_default: Area;
  updated_at: string | null;
  updated_by?: { display_name: string } | null;
}

let cache: Promise<ServiceAreaInfo | null> | null = null;
const listeners = new Set<() => void>();

export function loadServiceArea(): Promise<ServiceAreaInfo | null> {
  if (!cache) {
    cache = api.get<ServiceAreaInfo>("/api/v1/service-area").catch(() => {
      cache = null;
      return null;
    });
  }
  return cache;
}

export function invalidateServiceArea(): void {
  cache = null;
  listeners.forEach((f) => f());
}

export function onServiceAreaChanged(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export const inArea = (a: Area, p: { lat: number; lon: number }) =>
  p.lat >= a.south && p.lat <= a.north && p.lon >= a.west && p.lon <= a.east;
