import { MapPointsEditor } from "./MapPointsEditor";
import type { Point } from "../types";

/** Одна точка (база парка А) на карте района: метку можно перетащить или поставить щелчком.
 *  Координаты рядом с картой синхронизированы с меткой. */
export function PointPicker({ value, onChange, label = "А", height = 320 }: {
  value: Point; onChange: (p: Point) => void; label?: string; height?: number;
}) {
  return (
    <MapPointsEditor height={height}
                     pins={[{ key: "p", label, title: `${label} — база парка`, point: value, className: "map-base-pin" }]}
                     onChange={(_, p) => onChange({ ...value, lat: p.lat, lon: p.lon })}
                     emptyHint="Карт района нет — укажите координаты базы вручную (карту загружает администратор: «Карты»)." />
  );
}
