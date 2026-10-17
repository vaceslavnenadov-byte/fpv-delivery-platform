import { api } from "./api";

// Справочники администратора: типы грузов и типы (конфигурации) БПЛА.

export interface CargoType {
  id: string;
  code: string;
  name: string;
  category: string;
  max_weight_g: number;
  updated_at?: string | null;
}

export interface DroneModel {
  id: string;
  model: string;
  manufacturer: string;
  link_type: string;
  construction: string;
  engine_type: string;
  max_range_m: number;
  max_payload_g: number;
  updated_at?: string | null;
}

export const loadCargoTypes = () => api.get<{ items: CargoType[] }>("/api/v1/catalog/cargo-types").then((r) => r.items);
export const loadDroneModels = () => api.get<{ items: DroneModel[] }>("/api/v1/catalog/drone-models").then((r) => r.items);
