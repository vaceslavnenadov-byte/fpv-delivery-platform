import { Link } from "react-router";
import { api } from "../../api";
import { OrderTable } from "../../components/OrderTable";
import { Card, Pending, Stat } from "../../components/ui";
import { useLive } from "../../realtime";
import type { Order } from "../../types";

const ACTIVE = ["pending_confirmation", "awaiting_operator", "accepted", "awaiting_readiness", "ready", "in_progress",
  "awaiting_delivery_confirmation", "admin_review", "disputed"];

export function CustomerOrdersPage() {
  const { data } = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders?as=customer"), [],
    (m) => m.kind.startsWith("order."));
  if (!data) return <Pending />;
  const active = data.items.filter((o) => ACTIVE.includes(o.status));
  const history = data.items.filter((o) => !ACTIVE.includes(o.status));
  return (
    <div className="stack">
      <div className="row between">
        <h1>Мои заказы</h1>
        <Link className="btn primary" to="/new">Создать заказ</Link>
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
