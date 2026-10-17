import { Link } from "react-router";
import type { Order } from "../types";

/** Красные плашки «Приоритетный» / «Повторный» для повторного вылета. */
export function RepeatBadges({ o }: { o: Pick<Order, "priority" | "repeat_of"> }) {
  if (!o.priority && !o.repeat_of) return null;
  return (
    <span className="repeat-badges">
      {o.priority && <span className="flag-red">Приоритетный</span>}
      {o.repeat_of && <span className="flag-red">Повторный</span>}
    </span>
  );
}

/** Полоса вверху страницы заказа: это повторный вылет. */
export function RepeatBanner({ o }: { o: Pick<Order, "priority" | "repeat_of"> }) {
  if (!o.repeat_of) return null;
  return (
    <div className="repeat-banner" role="note">
      <span className="flag-red">Приоритетный</span><span className="flag-red">Повторный</span>
      <span>Повторный вылет заказа <Link to={`/orders/${o.repeat_of.id}`}>№{o.repeat_of.number}</Link> — выполняется в первую очередь</span>
    </div>
  );
}
