import { api } from "../../api";
import { Card, Pending } from "../../components/ui";
import { useLive } from "../../realtime";
import type { Order } from "../../types";
import { OrderTable } from "../../components/OrderTable";

export function CoordinatorPage() {
  const q = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders?as=coordinator"), [], (m) => m.kind.startsWith("order."));
  if (!q.data) return <Pending />;
  const disputed = q.data.items.filter((o) => o.status === "disputed");
  const active = q.data.items.filter((o) => !["completed", "rejected", "cancelled", "aborted_technical", "disputed"].includes(o.status));
  return (
    <div className="stack">
      <h1>Назначенные заказы</h1>
      <p className="muted">Откройте заказ, чтобы увидеть маршрут, контрольные точки, телеметрию, историю согласований,
        добавить замечание, зарегистрировать инцидент или передать сообщение оператору.</p>
      {disputed.length > 0 && (
        <Card title={`⚖ Претензии на рассмотрении (${disputed.length})`} className="dispute-card open">
          <p className="muted small">Откройте заказ и примите решение: «Удовлетворить претензию» — передать администратору,
            «Отклонить претензию» — закрыть спор.</p>
          <OrderTable orders={disputed} />
        </Card>
      )}
      <Card title="В работе"><OrderTable orders={active} /></Card>
      <Card title="История"><OrderTable orders={q.data.items.filter((o) => !active.includes(o) && !disputed.includes(o))} /></Card>
    </div>
  );
}
