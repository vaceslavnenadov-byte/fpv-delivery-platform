import { useEffect, useState } from "react";
import { loadMaps, onMapsChanged } from "../maps";
import { LOCAL, loadProviders, onProvidersChanged, type MapProviderInfo } from "../tiles";
import type { MapLayer } from "../types";

const KIND: Record<string, string> = {
  esri_imagery: "спутник", esri_streets: "улицы", esri_topo: "топография", google_roadmap: "схема",
  google_satellite: "спутник", yandex_map: "схема", dgis: "схема", osm: "схема",
};
const PREF = "fpv.newOrder.map";

/** Последняя карта, выбранная заказчиком, — предлагается в следующем заказе. */
export function preferredMapSource(): string {
  try {
    return localStorage.getItem(PREF) ?? "";
  } catch {
    return "";
  }
}

/** Выбор карты, на которой заказчик прокладывает маршрут. Онлайн-карта сохраняется в заказе и по
 *  умолчанию показывается оператору и координатору. */
export function MapSourcePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [layers, setLayers] = useState<MapLayer[] | null>(null);
  const [providers, setProviders] = useState<MapProviderInfo[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () => Promise.all([loadMaps(), loadProviders()]).then(([l, p]) => {
      if (!alive) return;
      setLayers(l.filter((m) => m.active));
      setProviders(p);
    });
    load();
    const off1 = onMapsChanged(load);
    const off2 = onProvidersChanged(load);
    return () => {
      alive = false;
      off1();
      off2();
    };
  }, []);
  if (layers === null) return null;

  const options = [
    ...providers.map((p) => ({ id: p.id, name: p.name, hint: KIND[p.preset] ? `онлайн-карта · ${KIND[p.preset]}` : "онлайн-карта" })),
    ...(layers.length ? [{ id: LOCAL, name: "Карта района", hint: layers.length > 1 ? `загруженные карты (${layers.length})` : "загруженная карта" }] : []),
  ];
  const pick = (id: string) => {
    onChange(id);
    try {
      localStorage.setItem(PREF, id);
    } catch {
      /* выбор не запоминается — не критично */
    }
  };

  return (
    <fieldset className="map-source">
      <legend>Карта для маршрута</legend>
      {options.length === 0 ? (
        <p className="muted small">Карты не подключены — укажите точки координатами ниже. Онлайн-карты (ESRI, Google,
          Яндекс, 2ГИС, OpenStreetMap) и карты района подключает администратор.</p>
      ) : (
        <>
          <div className="map-source-options" role="radiogroup" aria-label="Карта для маршрута">
            {options.map((o) => (
              <button key={o.id} type="button" role="radio" aria-checked={value === o.id}
                      className={value === o.id ? "selected" : ""} onClick={() => pick(o.id)}>
                <strong>{o.name}</strong>
                <small>{o.hint}</small>
              </button>
            ))}
          </div>
          <p className="muted small">
            {value && value !== LOCAL
              ? "Эту карту по умолчанию увидят оператор и координатор вашего заказа (они могут переключить её у себя)."
              : "Карту можно сменить в любой момент — точки маршрута сохранятся, они задаются координатами."}
          </p>
        </>
      )}
    </fieldset>
  );
}
