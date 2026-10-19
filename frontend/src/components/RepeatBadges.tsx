import { Link } from "react-router";
import type { Order } from "../types";

// Приоритет заказа: по грузополучателю (справочник «Типы грузополучателей»); повторный вылет — не ниже
// высокого. Порядок в списках: наивысший, повторный/высокий, высокий, обычный, низкий.

export const PRIORITY_LABELS: Record<string, string> = {
  low: "Низкий приоритет", normal: "Обычный приоритет", high: "Высокий приоритет", highest: "Наивысший приоритет",
};

type P = Pick<Order, "priority_level" | "repeat_of" | "status">;

/** Класс строки/карточки: наивысший приоритет — в красной рамке (пока заказ не завершён). */
export function priorityClass(o: P): string {
  const done = ["completed", "rejected", "cancelled", "aborted_technical"].includes(o.status);
  return o.priority_level === "highest" && !done ? "priority-highest" : "";
}

/** Плашки приоритета и повторного вылета. */
export function RepeatBadges({ o }: { o: Pick<Order, "priority_level" | "repeat_of"> }) {
  const level = o.priority_level ?? "normal";
  if (level === "normal" && !o.repeat_of) return null;
  return (
    <span className="repeat-badges">
      {level === "highest" && <span className="flag-red">Наивысший приоритет</span>}
      {level === "high" && <span className="flag-orange">Высокий приоритет</span>}
      {level === "low" && <span className="flag-muted">Низкий приоритет</span>}
      {o.repeat_of && <span className="flag-red">Повторный заказ</span>}
    </span>
  );
}

/** Полоса вверху страницы заказа: это повторный вылет. */
export function RepeatBanner({ o }: { o: Pick<Order, "priority_level" | "repeat_of"> }) {
  if (!o.repeat_of) return null;
  return (
    <div className="repeat-banner" role="note">
      <span className="flag-red">Повторный заказ</span>
      <span>Повторный вылет заказа <Link to={`/orders/${o.repeat_of.id}`}>№{o.repeat_of.number}</Link> — высокий приоритет</span>
    </div>
  );
}
