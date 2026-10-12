import { Link } from "react-router-dom";
import { api } from "../../api";
import { Badge, Card, Empty, Pending, Stat } from "../../components/ui";
import { CARGO_TYPES, fmtDate, fmtDistance } from "../../labels";
import { useLive } from "../../realtime";
import type { Order } from "../../types";

const ACTIVE = ["pending_confirmation", "awaiting_operator", "accepted", "awaiting_readiness", "ready", "in_progress",
  "awaiting_delivery_confirmation", "admin_review", "disputed"];

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

export function CustomerOrdersPage() {
  const { data } = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders"), [],
    (m) => m.kind.startsWith("order."));
  if (!data) return <Pending />;
  const active = data.items.filter((o) => ACTIVE.includes(o.status));
  const history = data.items.filter((o) => !ACTIVE.includes(o.status));
  return (
    <div className="stack">
      <div className="row between">
        <h1>Мои заказы</h1>
        <Link className="btn primary" to="/customer/new">Создать заказ</Link>
      </div>
      <div className="stats">
        <Stat label="Активные" value={active.length} />
        <Stat label="Выполнены" value={data.items.filter((o) => o.status === "completed").length} />
        <Stat label="Черновики" value={data.items.filter((o) => o.status === "draft").length} />
      </div>
      <Card title="Активные заказы"><OrderTable orders={active} /></Card>
      <Card title="История заказов"><OrderTable orders={history} /></Card>
    </div>
  );
}
