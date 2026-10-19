import { RepeatBadges, priorityClass } from "../../components/RepeatBadges";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { openFlightMonitor } from "../OrderDetail";
import { api, errorText, newIdempotencyKey } from "../../api";
import { Alert, Badge, Card, Empty, Pending, Stat } from "../../components/ui";
import { cargoName, fmtDate, fmtDistance } from "../../labels";
import { useLive } from "../../realtime";
import type { Order } from "../../types";
import { OrderTable } from "../../components/OrderTable";
import { routeRows } from "../../components/OrderCargo";
import { useReadiness } from "../../components/Readiness";

// Заказы назначает система (готовому оператору с квалификацией и наименьшей нагрузкой);
// оператор принимает предложение или отклоняет — тогда заказ уходит другому оператору.
export function OperatorQueuePage() {
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const q = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders/queue"), [],
    (m) => m.kind.startsWith("order."));
  const readiness = useReadiness("operator");
  const notReady = readiness?.ready === false;
  useEffect(() => { if (readiness) q.reload(); }, [readiness?.ready]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function accept(o: Order) {
    try {
      await api.post(`/api/v1/orders/${o.id}/accept`, { expected_version: o.version }, { idempotencyKey: newIdempotencyKey() });
      setMsg({ tone: "ok", text: `Заказ №${o.number} принят. Перейдите в «Мои задания» для проверки готовности.` });
      q.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  async function decline(o: Order) {
    const reason = prompt("Причина отклонения (заказ будет предложен другому оператору)");
    if (reason === null) return;
    try {
      await api.post(`/api/v1/orders/${o.id}/decline`, { reason, expected_version: o.version });
      setMsg({ tone: "ok", text: `Заказ №${o.number} отклонён — он предложен другому оператору.` });
      q.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  return (
    <div className="stack">
      <h1>Предложенные заказы</h1>
      <p className="muted">Заказы распределяет система: среди операторов на смене, с квалификацией на тип БПЛА, с учётом
        нагрузки. Примите заказ в работу или отклоните его — тогда он уйдёт другому оператору.</p>
      {notReady && <Alert tone="warn">Вы отметили «Не готов к работе» — новые заказы вам не предлагаются, непринятые переданы
        другим операторам. Чтобы снова получать заказы, нажмите «Готов к работе» вверху экрана.</Alert>}
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title={`Предложены вам${q.data ? ` (${q.data.items.length})` : ""}`}>
        {!q.data ? <Pending /> : q.data.items.length === 0 ? <Empty>Сейчас вам не предложено ни одного заказа</Empty> : (
          <div className="cards">
            {q.data.items.map((o) => (
              <article key={o.id} className={`order-card ${priorityClass(o)}`}>
                <div className="row between"><Link to={`/orders/${o.id}`}><strong>№{o.number}</strong></Link> <RepeatBadges o={o} /><Badge kind="order" value={o.status} /></div>
                <div>{cargoName(o)}, {o.cargo_weight_g} г — {o.cargo_description}</div>
                <div className="small">Грузополучатель: <strong>{o.consignee?.name ?? o.recipient_name ?? "—"}</strong></div>
                {o.drone_waits_for && <div className="small warn-text">На этом дроне раньше вылетает заказ №{o.drone_waits_for} — принять можно после него</div>}
                <div className="small">{routeRows(o).map(([k, v]) => <div key={k}>{k}: {v}</div>)}</div>
                <div className="small muted">{fmtDistance(o.route?.distance_m)} · {o.asap ? "как можно скорее" : `окно ${fmtDate(o.window_start)} — ${fmtDate(o.window_end)}`}</div>
                <div className="row">
                  <button className="btn primary small" onClick={() => accept(o)}>Принять</button>
                  <button className="btn small" onClick={() => decline(o)}>Отклонить</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export function OperatorMinePage() {
  const q = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders?as=operator"), [], (m) => m.kind.startsWith("order."));
  if (!q.data) return <Pending />;
  const mine = q.data.items;
  const active = mine.filter((o) => ["accepted", "awaiting_readiness", "ready", "in_progress"].includes(o.status));
  return (
    <div className="stack">
      <h1>Мои задания</h1>
      {active.length > 0 && (
        <Alert tone="info">
          <div className="row between">
            <span>Активное задание: <Link to={`/orders/${active[0].id}`}>№{active[0].number}</Link> — откройте карточку для проверки готовности, начала и завершения.</span>
            <button className="btn primary small" onClick={() => openFlightMonitor(active[0].id)}>🛰 Монитор полёта</button>
          </div>
        </Alert>
      )}
      <Card title="Активные"><OrderTable orders={active} /></Card>
      <Card title="Завершённые и на проверке"><OrderTable orders={mine.filter((o) => !active.includes(o))} /></Card>
    </div>
  );
}

interface ScoreSummary {
  total_points: number;
  completed_orders: number;
  rating: number | null;
  transactions: { id: number; order_id: string; kind: string; points: number; reason: string; created_at: string }[];
}

export function OperatorScoresPage() {
  const s = useLive(() => api.get<ScoreSummary>("/api/v1/scores/me"), [], (m) => m.kind.startsWith("score."));
  const rules = useLive(() => api.get<{ version: number; rules: { time_window: Record<string, number>; result: Record<string, number> } }>("/api/v1/admin/scoring-rules"), []);
  if (!s.data) return <Pending />;
  return (
    <div className="stack">
      <h1>Баллы и рейтинг</h1>
      <div className="stats">
        <Stat label="Всего баллов" value={s.data.total_points} />
        <Stat label="Выполнено заказов" value={s.data.completed_orders} />
        <Stat label="Средняя оценка" value={s.data.rating ?? "—"} hint="по окончательным начислениям" />
      </div>
      <Card title="Начисления">
        {s.data.transactions.length === 0 ? <Empty>Начислений пока нет</Empty> : (
          <table className="table">
            <thead><tr><th>Дата</th><th>Заказ</th><th>Тип</th><th className="num">Баллы</th><th>Основание</th></tr></thead>
            <tbody>{s.data.transactions.map((t) => (
              <tr key={t.id}><td className="small">{fmtDate(t.created_at)}</td><td><Link to={`/orders/${t.order_id}`}>открыть</Link></td>
                <td>{t.kind === "final" ? "Окончательное" : "Корректировка"}</td><td className="num">{t.points}</td><td className="small">{t.reason}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {rules.data && (
        <Card title={`Правила начисления (версия ${rules.data.version})`}>
          <ul className="list">
            <li>Соблюдение временного окна — до {rules.data.rules.time_window.max_points} баллов (снижение линейно до 0 при отклонении {rules.data.rules.time_window.grace_minutes} мин)</li>
            <li>Подтверждение получения — {rules.data.rules.result.receipt_confirmed}</li>
            <li>Отчёт о выполнении — {rules.data.rules.result.report_submitted}</li>
            <li>Проверенное видео — {rules.data.rules.result.video_verified}</li>
            <li>Подтверждение администратора — {rules.data.rules.result.admin_approved}</li>
            <li>Обоснованная претензия — минус {rules.data.rules.result.upheld_complaint_penalty}</li>
          </ul>
        </Card>
      )}
    </div>
  );
}
