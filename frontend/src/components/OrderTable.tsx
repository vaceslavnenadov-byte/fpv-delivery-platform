import { Link } from "react-router";
import { RepeatBadges, priorityClass } from "./RepeatBadges";
import { Badge, Empty } from "./ui";
import { cargoName, fmtDate, fmtDistance } from "../labels";
import type { Order } from "../types";

export function OrderTable({ orders, showCustomer = false }: { orders: Order[]; showCustomer?: boolean }) {
  if (!orders.length) return <Empty>Заказов нет</Empty>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>№</th><th>Статус</th><th>Груз</th><th>Грузополучатель</th><th>Маршрут</th><th>Время доставки</th><th className="num">Стоимость</th>{showCustomer && <th>Создан</th>}</tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id} className={priorityClass(o)}>
              <td><Link to={`/orders/${o.id}`}>№{o.number}</Link> <RepeatBadges o={o} /></td>
              <td><Badge kind="order" value={o.status} />
                {o.status === "in_progress" && (
                  <> <Link to={`/orders/${o.id}`} className="live-badge" title="Смотреть трансляцию с дрона">● В ЭФИРЕ</Link></>
                )}</td>
              <td>{cargoName(o)}, {o.cargo_weight_g} г</td>
              <td className="small">{o.consignee?.name ?? o.recipient_name ?? "—"}</td>
              <td className="small">{routeText(o)}<br />
                <span className="muted">{fmtDistance(o.route?.distance_m)}</span></td>
              <td className="small">{o.asap ? "как можно скорее" : `${fmtDate(o.window_start)} — ${fmtDate(o.window_end)}`}</td>
              <td className="num">{o.quote_amount ? `${o.quote_amount} ₽` : "—"}</td>
              {showCustomer && <td className="small">{fmtDate(o.created_at)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Краткий маршрут: «А (база) → Точка В» для заказа по точкам, иначе «Б → В». */
export function routeText(o: Order): string {
  const r = o.route;
  if (!r) return "—";
  if (r.pickup_at_base) return `${r.base?.address ?? "База"} → ${r.destination.address}${r.stops?.length ? ` (+${r.stops.length})` : ""}`;
  return `${r.origin.address} → ${r.destination.address}`;
}
