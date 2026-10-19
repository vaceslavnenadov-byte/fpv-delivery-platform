import type { ReactNode } from "react";
import { ChatImage, Card, KV } from "./ui";
import { POINT_LETTERS } from "./RouteMap";
import { cargoName } from "../labels";
import type { Drone, Order } from "../types";

/** Грузы заказа: тип, описание, масса и габариты каждого груза. */
export function CargoItems({ o }: { o: Order }) {
  const items = o.cargo_items?.length ? o.cargo_items : [{
    position: 1, cargo_type: o.cargo_type, cargo_type_name: cargoName(o), description: o.cargo_description,
    weight_g: o.cargo_weight_g, dims_mm: o.cargo_dims_mm,
  }];
  return (
    <div className="table-wrap">
      <table className="table compact cargo-table">
        <thead><tr><th>№</th><th>Тип груза</th><th>Описание</th><th className="num">Масса, г</th><th>Габариты, мм</th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.position}><td>{i.position}</td><td>{i.cargo_type_name}</td><td>{i.description}</td>
              <td className="num">{i.weight_g}</td><td>{i.dims_mm.join("×")}</td></tr>
          ))}
          {items.length > 1 && (
            <tr className="total"><td /><td colSpan={2}><strong>Итого: {items.length} груза(ов)</strong></td>
              <td className="num"><strong>{o.cargo_weight_g}</strong></td><td /></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Параметры дрона заказа (из справочника «Типы БПЛА» или введённые техником). */
export function DroneParams({ d }: { d: Drone }) {
  return (
    <KV items={[
      ["Модель", d.model],
      ["Производитель", d.manufacturer],
      ["Учётный номер", d.registration_number],
      ["Серийный номер", d.serial_number],
      ["Тип связи", d.link_type || "—"],
      ["Конструкция", d.construction || "—"],
      ["Тип двигателя", d.engine_type || "—"],
      ["Дальность", `${(d.max_range_m / 1000).toFixed(1)} км`],
      ["Грузоподъёмность", `${d.max_payload_g} г`],
    ]} />
  );
}

export function OrderCargoCards({ o }: { o: Order }) {
  return (
    <div className="grid2">
      <Card title={`Груз${(o.cargo_items?.length ?? 1) > 1 ? `ы (${o.cargo_items!.length})` : ""}`}><CargoItems o={o} /></Card>
      {o.drone && <Card title="Параметры дрона"><DroneParams d={o.drone} /></Card>}
    </div>
  );
}

/** Строки маршрута для карточек: по точкам заказчика (А — база с грузом, Точка Б, В, …) или по-старому. */
export function routeRows(o: Order): [string, ReactNode][] {
  const r = o.route;
  if (!r) return [];
  const stops = r.stops ?? [];
  const txt = (x: { address?: string; note?: string }) => (x.note ? `${x.address} — ${x.note}` : x.address ?? "");
  if (r.pickup_at_base) {
    return [
      ["А — база парка (груз)", r.base?.address ?? r.origin.address],
      ...stops.map((x, i) => [`Точка ${POINT_LETTERS[i]} — промежуточная`, txt(x)] as [string, ReactNode]),
      [`Точка ${POINT_LETTERS[stops.length]} — грузополучатель`, r.destination.address],
    ];
  }
  if (r.base) {
    return [
      ["А — база парка", r.base.address],
      ["Б — забор груза", r.origin.address],
      ...stops.map((x, i) => [`${i + 1} — промежуточная точка`, txt(x)] as [string, ReactNode]),
      ["В — получатель", r.destination.address],
    ];
  }
  return [["Точка А", r.origin.address], ["Точка Б", r.destination.address]];
}

/** Грузополучатель заказа (сведения из справочника на момент заказа). */
export function ConsigneeCard({ o }: { o: Order }) {
  const c = o.consignee;
  return (
    <Card title="Грузополучатель">
      {c ? (
        <KV items={[
          ["Название", c.name],
          ["Тип", c.kind || "—"],
          ["Местоположение", c.location || "—"],
          ["Приоритет", c.priority_label],
          ["Рекомендованный груз", c.recommended_cargo_name ?? "—"],
          ["Комментарий", c.comment || "—"],
        ]} />
      ) : <KV items={[["Получатель", o.recipient_name ?? "—"], ["Телефон", o.recipient_phone ?? "—"]]} />}
    </Card>
  );
}

/** Комментарий заказчика к заказу (и фото), если есть. */
export function CustomerComment({ o }: { o: Order }) {
  if (!o.customer_comment && !o.has_comment_photo) return null;
  return (
    <Card title="Комментарий заказчика">
      {o.customer_comment && <p className="pre-wrap">{o.customer_comment}</p>}
      {o.has_comment_photo && <ChatImage src={`/api/v1/orders/${o.id}/comment-photo`} alt="Фото к заказу" className="support-media" />}
    </Card>
  );
}
