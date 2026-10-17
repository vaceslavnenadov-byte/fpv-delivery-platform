import { api } from "../../api";
import { Card, Pending } from "../../components/ui";
import { useLive } from "../../realtime";
import type { Order, RepeatRequest } from "../../types";
import { Link } from "react-router";
import { fmtDate } from "../../labels";
import { OrderTable } from "../../components/OrderTable";

export function CoordinatorPage() {
  const q = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders?as=coordinator"), [], (m) => m.kind.startsWith("order."));
  const repeats = useLive(() => api.get<{ items: (RepeatRequest & { order_number: number | null })[] }>("/api/v1/orders/repeat-requests"),
    [], (m) => m.kind.startsWith("order."));
  if (!q.data) return <Pending />;
  const disputed = q.data.items.filter((o) => o.status === "disputed");
  const active = q.data.items.filter((o) => !["completed", "rejected", "cancelled", "aborted_technical", "disputed"].includes(o.status));
  return (
    <div className="stack">
      <h1>Назначенные заказы</h1>
      <p className="muted">Откройте заказ, чтобы увидеть маршрут, контрольные точки, телеметрию, историю согласований,
        добавить замечание, зарегистрировать инцидент или передать сообщение оператору.</p>
      {(repeats.data?.items ?? []).length > 0 && (
        <Card title={`🔁 Запросы на повторный вылет (${repeats.data!.items.length})`} className="dispute-card open">
          <ul className="repeat-list">
            {repeats.data!.items.map((r) => (
              <li key={r.id}><Link to={`/orders/${r.order_id}`}>Заказ №{r.order_number}</Link> — {r.requested_by?.display_name},{" "}
                {fmtDate(r.created_at)}{r.comment && <span className="muted"> · «{r.comment}»</span>}</li>
            ))}
          </ul>
          <p className="muted small">Откройте заказ и нажмите «Согласовать» или «Отклонить». Согласованный повторный вылет
            создаёт приоритетный заказ — он идёт первым у операторов, техника и у вас.</p>
        </Card>
      )}
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
