import { Link } from "react-router";
import { Badge, Empty } from "./ui";
import { CARGO_TYPES, fmtDate, fmtDistance } from "../labels";
import type { Order } from "../types";

export function OrderTable({ orders, showCustomer = false }: { orders: Order[]; showCustomer?: boolean }) {
  if (!orders.length) return <Empty>Заказов нет</Empty>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>№</th><th>Статус</th><th>Груз</th><th>Маршрут</th><th>Окно доставки</th><th>Стоимость</th>{showCustomer && <th>Создан</th>}</tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td><Link to={`/orders/${o.id}`}>№{o.number}</Link></td>
              <td><Badge kind="order" value={o.status} /></td>
              <td>{CARGO_TYPES[o.cargo_type] ?? o.cargo_type}, {o.cargo_weight_g} г</td>
              <td className="small">{o.route?.origin.address} → {o.route?.destination.address}<br />
                <span className="muted">{fmtDistance(o.route?.distance_m)}</span></td>
              <td className="small">{fmtDate(o.window_start)} — {fmtDate(o.window_end)}</td>
              <td className="num">{o.quote_amount ? `${o.quote_amount} ₽` : "—"}</td>
              {showCustomer && <td className="small">{fmtDate(o.created_at)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
