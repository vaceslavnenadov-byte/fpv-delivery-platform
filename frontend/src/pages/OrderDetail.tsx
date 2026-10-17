import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, errorText, newIdempotencyKey } from "../api";
import { appUrl } from "../base";
import { useAuth } from "../auth";
import { CUSTOMER_STREAM_STATUSES, CustomerCameraPublisher, CustomerCameraViewer } from "../components/CustomerCamera";
import { LiveVideo } from "../components/LiveVideo";
import { OrderChat } from "../components/OrderChat";
import { RepeatBadges, RepeatBanner } from "../components/RepeatBadges";
import { RouteMap } from "../components/RouteMap";
import { ScoreCard } from "../components/ScoreCard";
import { Timeline } from "../components/Timeline";
import { Alert, Badge, Card, KV, Pending, Tone } from "../components/ui";
import { CARGO_TYPES, fmtDate, fmtDistance } from "../labels";
import { useLive } from "../realtime";
import type { Complaint, HistoryItem, Note, Order, RepeatRequest, Score, Telemetry, UserBrief } from "../types";

interface Confirmation { kind: string; decision: string; comment: string; report: Record<string, string>; actor: UserBrief | null; created_at: string }

const NOTE_KIND: Record<string, string> = {
  remark: "Замечание", message_to_operator: "Сообщение оператору", incident: "Инцидент", deviation: "Отклонение",
  chat: "Переписка",
};

/** Статусы, в которых оператор работает с заданием и нужен монитор полёта. */
const MONITOR_STATUSES = ["accepted", "awaiting_readiness", "ready", "in_progress", "awaiting_delivery_confirmation"];

export function openFlightMonitor(orderId: string) {
  // Отдельное окно на каждый заказ: повторное нажатие переводит фокус на уже открытое окно.
  const w = window.open(appUrl(`/orders/${orderId}/monitor`), `fpv-monitor-${orderId}`,
    "popup,width=1440,height=900,left=40,top=40");
  w?.focus();
}
const CONF_KIND: Record<string, string> = {
  operator_report: "Отчёт оператора", recipient_receipt: "Подтверждение получения", admin_review: "Решение администратора",
};

export function OrderDetailPage() {
  const { id } = useParams();
  const { me, can } = useAuth();
  const orderId = id!;
  const isOrderEvent = (m: { kind: string; data: Record<string, unknown> }) =>
    m.data?.order_id === orderId || m.kind === "drone.link";
  const order = useLive(() => api.get<Order>(`/api/v1/orders/${orderId}`), [orderId], isOrderEvent);
  const history = useLive(() => api.get<{ items: HistoryItem[] }>(`/api/v1/orders/${orderId}/history`), [orderId], isOrderEvent);
  const score = useLive(() => api.get<Score>(`/api/v1/orders/${orderId}/score`), [orderId], isOrderEvent);
  const confs = useLive(() => api.get<{ items: Confirmation[] }>(`/api/v1/orders/${orderId}/confirmations`), [orderId], isOrderEvent);
  const o = order.data;
  const canTelemetry = !!o && (can("telemetry.read_all") ||
    (can("telemetry.read_assigned") && (o.operator_id === me?.id || o.coordinator_id === me?.id)));
  const telemetry = useLive(
    () => (canTelemetry ? api.get<{ items: Telemetry[]; video_buffer: Record<string, number> | null }>(`/api/v1/orders/${orderId}/telemetry`)
      : Promise.resolve({ items: [], video_buffer: null })),
    [orderId, canTelemetry], (m) => m.data?.order_id === orderId);
  const showNotes = !!o && o.customer_id !== me?.id;
  const notes = useLive(() => (showNotes ? api.get<{ items: Note[] }>(`/api/v1/orders/${orderId}/notes`) : Promise.resolve({ items: [] })),
    [orderId, showNotes], (m) => m.data?.order_id === orderId);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (order.error) return <Alert>{errorText(order.error)}</Alert>;
  if (!o) return <Pending />;

  const reloadAll = () => { order.reload(); history.reload(); score.reload(); confs.reload(); telemetry.reload(); notes.reload(); };

  async function act(path: string, body: unknown = {}, okText?: string, idem = true) {
    setBusy(true);
    setMsg(null);
    try {
      await api.post(`/api/v1/orders/${orderId}/${path}`, body, idem ? { idempotencyKey: newIdempotencyKey() } : undefined);
      if (okText) setMsg({ tone: "ok", text: okText });
      reloadAll();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function decideRepeat(id: number, approve: boolean, comment: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<RepeatRequest>(`/api/v1/orders/repeat-requests/${id}/decision`, { approve, comment });
      setMsg({ tone: "ok", text: approve ? `Повторный вылет согласован — создан приоритетный заказ №${r.new_order?.number}` : "Повторный вылет отклонён" });
      reloadAll();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function readiness() {
    setBusy(true);
    try {
      const r = await api.post<{ ready: boolean; reasons: string[] }>(`/api/v1/orders/${orderId}/readiness-check`);
      setMsg(r.ready ? { tone: "ok", text: "Проверка готовности пройдена" }
        : { tone: "warn", text: `Не готов: ${r.reasons.join("; ")}` });
      reloadAll();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const isCustomer = o.customer_id === me?.id;
  const isOperator = o.operator_id === me?.id;
  const last = telemetry.data?.items[telemetry.data.items.length - 1];
  const v = { expected_version: o.version };

  return (
    <div className="stack">
      <RepeatBanner o={o} />
      <div className="row between">
        <div>
          <h1>Заказ №{o.number} <Badge kind="order" value={o.status} /> <RepeatBadges o={o} /></h1>
          <div className="muted small">Создан {fmtDate(o.created_at)} · версия {o.version}{o.receipt_overdue && " · получение не подтверждено в срок"}</div>
        </div>
        {canTelemetry && MONITOR_STATUSES.includes(o.status) && (
          <button className="btn primary" onClick={() => openFlightMonitor(o.id)}>🛰 Открыть монитор полёта</button>
        )}
      </div>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      {(REPEATABLE.includes(o.status) && isCustomer && can("order.repeat.request")) || (o.repeat_requests ?? []).length > 0 ? (
        <RepeatCard o={o} busy={busy} canRequest={REPEATABLE.includes(o.status) && isCustomer && can("order.repeat.request")}
                    canDecide={can("order.repeat.decide") && (o.coordinator_id === me?.id || can("order.read_all"))}
                    onRequest={(comment) => act("repeat", { comment }, "Запрос повторного вылета отправлен координатору", false)}
                    onDecide={(id, approve, comment) => decideRepeat(id, approve, comment)} />
      ) : null}

      {o.dispute && (
        <DisputeCard d={o.dispute} busy={busy}
                     canReview={can("complaint.review") && o.coordinator_id === me?.id && o.status === "disputed"}
                     onReview={(upheld, comment) => act("dispute/review", { upheld, comment },
                       upheld ? "Претензия удовлетворена и передана администратору" : "Претензия отклонена", false)} />
      )}

      {isCustomer && can("video.live") && CUSTOMER_STREAM_STATUSES.includes(o.status) && (
        <Card title="📡 Трансляция с дрона">
          {o.status === "in_progress" ? (
            // Заказчик смотрит свой полёт сразу, без лишних действий; открытие трансляции фиксируется в аудите.
            <LiveVideo key={o.id} orderId={o.id} autoStart />
          ) : o.status === "awaiting_delivery_confirmation" ? (
            <p className="muted">Полёт завершён — трансляция окончена. Видеоподтверждение доставки — ниже, в блоке «Видеоподтверждение».</p>
          ) : (
            <p className="muted">Трансляция с камеры дрона начнётся сама, когда оператор начнёт выполнение заказа —
              страницу обновлять не нужно. Сейчас: {o.status_label}.</p>
          )}
        </Card>
      )}

      {!["draft", "pending_confirmation", "awaiting_operator"].includes(o.status) && can("order.chat") &&
        (isCustomer || o.operator_id === me?.id || o.coordinator_id === me?.id || can("order.read_all")) && (
        <OrderChat order={o} flying={o.status === "in_progress"} />
      )}

      <div className="grid2">
        <Card title="Маршрут и статус">
          {o.route && <RouteMap route={o.route} track={telemetry.data?.items ?? []} status={o.status_label}
                                  providerId={o.map_provider_id} orderId={o.id} />}
          {o.route?.approval_notes?.length ? (
            <ul className="checks">{o.route.approval_notes.map((c) => <li key={c.code} className="fail">✕ {c.message}</li>)}</ul>
          ) : null}
        </Card>
        <Card title="Сведения о заказе">
          <KV items={[
            ["Груз", `${CARGO_TYPES[o.cargo_type] ?? o.cargo_type}: ${o.cargo_description}`],
            ["Масса / габариты", `${o.cargo_weight_g} г, ${o.cargo_dims_mm.join("×")} мм`],
            ...(o.route?.base ? [
              ["А — база парка", o.route.base.address],
              ["Б — забор груза", o.route.origin.address],
              ...((o.route.stops ?? []).map((x, i) => [`${i + 1} — промежуточная точка`, x.note ? `${x.address} — ${x.note}` : x.address]) as [string, string][]),
              ["В — получатель", o.route.destination.address],
            ] as [string, string | undefined][] : [
              ["Точка А", o.route?.origin.address],
              ["Точка Б", o.route?.destination.address],
            ] as [string, string | undefined][]),
            ["Расстояние", fmtDistance(o.route?.distance_m)],
            ["Готовность дрона (техник парка)", o.tech_ready_at
              ? <Tone tone="ok">подтверждена {fmtDate(o.tech_ready_at)}</Tone>
              : ["awaiting_operator", "accepted", "awaiting_readiness", "ready"].includes(o.status)
                ? <Tone tone="warn">ожидает подтверждения техника</Tone> : "—"],
            ["Временное окно", `${fmtDate(o.window_start)} — ${fmtDate(o.window_end)}`],
            ["Получатель", `${o.recipient_name ?? "—"}, ${o.recipient_phone ?? ""}`],
            ["Стоимость", o.quote_amount ? `${o.quote_amount} ₽` : "—"],
            ["Дрон", o.drone ? <>{o.drone.registration_number} <Badge kind="health" value={o.drone.health_status} /> <Badge kind="link" value={o.drone.link_status} /></> : "—"],
            ["Заказчик", o.customer?.display_name],
            ["Оператор", o.operator?.display_name ?? "не назначен"],
            ["Координатор", o.coordinator?.display_name ?? "не назначен"],
            ["Завершение доставки", fmtDate(o.delivered_at)],
            ["Причина закрытия", o.closed_reason],
          ]} />
        </Card>
      </div>

      <div className="grid2">
        <Card title="Действия">
          <div className="actions">
            {/* --- заказчик --- */}
            {isCustomer && o.status === "draft" && (
              <button className="btn primary" disabled={busy} onClick={() => act("submit", v, "Заказ проверен")}>Отправить на проверку</button>
            )}
            {isCustomer && o.status === "pending_confirmation" && (
              <button className="btn primary" disabled={busy} onClick={() => act("confirm", v, "Заказ передан операторам")}>Подтвердить условия</button>
            )}
            {(isCustomer || can("order.review")) && ["draft", "pending_confirmation", "awaiting_operator", "accepted", "awaiting_readiness", "ready"].includes(o.status) && (
              <ReasonAction label="Отменить заказ" busy={busy} onSubmit={(reason) => act("cancel", { ...v, reason }, "Заказ отменён")} />
            )}
            {isCustomer && can("order.confirm_receipt") && o.status === "awaiting_delivery_confirmation" && (
              <>
                <button className="btn primary" disabled={busy} onClick={() => act("receipt", { received: true, comment: "" }, "Получение подтверждено")}>Груз получен</button>
                <ReasonAction label="Груз не получен" busy={busy} required
                              onSubmit={(comment) => act("receipt", { received: false, comment }, "Открыт спор")} />
              </>
            )}
            {isCustomer && can("complaint.create") && ["admin_review", "completed"].includes(o.status) && (
              <ReasonAction label="Открыть спор" busy={busy} required minLength={5}
                            onSubmit={(description) => act("dispute", { description }, "Спор передан на рассмотрение", false)} />
            )}

            {/* --- оператор --- */}
            {can("order.accept") && o.status === "awaiting_operator" && (
              <>
                <button className="btn primary" disabled={busy} onClick={() => act("accept", v, "Заказ принят")}>Принять заказ</button>
                <ReasonAction label="Отклонить" busy={busy} onSubmit={(reason) => act("decline", { ...v, reason }, "Заказ скрыт из вашей очереди")} />
              </>
            )}
            {isOperator && ["accepted", "awaiting_readiness", "ready"].includes(o.status) && (
              <>
                <button className="btn" disabled={busy} onClick={readiness}>Проверить готовность</button>
                <ReasonAction label="Отказаться от задания" busy={busy} onSubmit={(reason) => act("decline", { ...v, reason }, "Заказ возвращён в очередь")} />
              </>
            )}
            {isOperator && o.status === "ready" && (
              <button className="btn primary" disabled={busy} onClick={() => act("start", v, "Задание начато")}>Начать задание</button>
            )}
            {isOperator && o.status === "in_progress" && (
              <button className="btn primary" disabled={busy}
                      onClick={() => confirm("Зафиксировать завершение доставки? Будут сохранены последние 15 секунд видео.") && act("complete", {}, "Доставка завершена")}>
                Зафиксировать завершение доставки
              </button>
            )}
            {isOperator && ["awaiting_readiness", "ready", "in_progress"].includes(o.status) && (
              <ReasonAction label="Прервать задание" busy={busy} required danger
                            onSubmit={(reason) => act("abort", { reason }, "Задание прервано")} />
            )}
            {isOperator && can("order.report") && ["awaiting_delivery_confirmation", "admin_review", "aborted_technical", "disputed"].includes(o.status)
              && !(confs.data?.items ?? []).some((c) => c.kind === "operator_report") && (
              <ReportForm busy={busy} onSubmit={(r) => act("report", r, "Отчёт сформирован", false)} />
            )}
            {isOperator && can("score.appeal") && ["completed", "rejected"].includes(o.status) && (
              <ReasonAction label="Обжаловать оценку" busy={busy} required minLength={5}
                            onSubmit={(description) => act("appeal", { description }, "Апелляция отправлена", false)} />
            )}

            {/* --- администратор --- */}
            {can("order.assign") && !["completed", "rejected", "cancelled", "aborted_technical"].includes(o.status) && (
              <AssignCoordinator current={o.coordinator_id} onAssign={(cid) => act("coordinator", { coordinator_id: cid }, "Координатор назначен", false)} />
            )}
            {can("order.review") && o.status === "admin_review" && <ReviewPanel order={o} busy={busy} onDone={reloadAll} setMsg={setMsg} />}
          </div>
          {!busy && (
            <p className="muted small">Доступные действия определяются вашей ролью и статусом заказа; сервер повторно проверяет права и допустимость каждого перехода.</p>
          )}
        </Card>
        <Card title="Хронология статусов">
          {history.data ? <Timeline items={history.data.items} /> : <Pending />}
        </Card>
      </div>

      {canTelemetry && (
        <Card title="Телеметрия и связь">
          {last ? (
            <div className="stats">
              <div className="stat"><div className="stat-value">{last.battery_pct ?? "—"}%</div><div className="stat-label">Батарея</div></div>
              <div className="stat"><div className="stat-value">{last.alt_m ?? "—"} м</div><div className="stat-label">Высота</div></div>
              <div className="stat"><div className="stat-value">{last.link_quality ?? "—"}</div><div className="stat-label">Качество связи</div></div>
              <div className="stat"><div className="stat-value">{last.flight_phase ?? "—"}</div><div className="stat-label">Фаза</div></div>
              <div className="stat"><div className="stat-value small">{fmtDate(last.recorded_at)}</div><div className="stat-label">Последние данные</div></div>
              {telemetry.data?.video_buffer && (
                <div className="stat"><div className="stat-value">{telemetry.data.video_buffer.covered_seconds} с</div>
                  <div className="stat-label">Видеобуфер ({telemetry.data.video_buffer.segments} сегм.)</div></div>
              )}
            </div>
          ) : <p className="muted">Телеметрия по заказу ещё не поступала.</p>}
          {o.drone && o.drone.battery_pct != null && o.drone.battery_pct < 30 && <Alert tone="warn">Низкий заряд батареи дрона</Alert>}
          {o.drone && ["lost", "degraded"].includes(o.drone.link_status) && (
            <Alert tone="danger">Нарушена связь с дроном. При потере связи дольше установленного порога задание будет прервано автоматически, дрон — переведён в «Требует проверки».</Alert>
          )}
        </Card>
      )}

      {CUSTOMER_STREAM_STATUSES.includes(o.status) && isCustomer && can("video.publish") && (
        <Card title="Трансляция с вашей камеры">
          <CustomerCameraPublisher key={o.id} orderId={o.id} />
        </Card>
      )}
      {CUSTOMER_STREAM_STATUSES.includes(o.status) && !isCustomer && can("video.live") && (
        <Card title="Камера заказчика">
          <CustomerCameraViewer key={o.id} orderId={o.id} />
        </Card>
      )}

      {o.status === "in_progress" && can("video.live") && !isCustomer && (
        <Card title="Прямая трансляция">
          <LiveVideo key={o.id} orderId={o.id} />
        </Card>
      )}

      <div className="grid2">
        <Card title="Видеоподтверждение">
          {!o.video ? <p className="muted">Будет создано при событии завершения доставки (последние 15 секунд видеопотока).</p> : (
            <>
              <KV items={[
                ["Статус", <Tone tone={o.video.status === "stored" ? "ok" : o.video.status === "missing" ? "danger" : "warn"}>{o.video.status}</Tone>],
                ["Причина отсутствия", o.video.missing_reason],
                ["Интервал", o.video.clip_start_at ? `${fmtDate(o.video.clip_start_at)} — ${fmtDate(o.video.clip_end_at)}` : "—"],
                ["Сегментов", o.video.segment_count],
                ["Проверка", o.video.review_status],
                ["Хранить до", fmtDate(o.video.retention_until)],
              ]} />
              {can("video.read") && o.video.status === "stored" && <VideoPlayer id={o.video.id} hasPreview={!!o.video.has_preview} />}
            </>
          )}
        </Card>
        <Card title="Оценка выполнения">
          {score.data ? <ScoreCard score={score.data} /> : <Pending />}
        </Card>
      </div>

      <div className="grid2">
        <Card title="Подтверждения и отчёты">
          {(confs.data?.items ?? []).length === 0 ? <p className="muted">Нет записей</p> : (
            <ul className="list">
              {confs.data!.items.map((c) => (
                <li key={c.kind}><strong>{CONF_KIND[c.kind] ?? c.kind}</strong> — {c.decision} · {c.actor?.display_name ?? "система"} · {fmtDate(c.created_at)}
                  {c.comment && <div className="small">{c.comment}</div>}
                  {c.report?.remarks && <div className="small muted">{c.report.remarks}</div>}
                </li>
              ))}
            </ul>
          )}
        </Card>
        {showNotes && (
          <Card title="Координация: замечания и инциденты">
            {(notes.data?.items ?? []).length === 0 && <p className="muted">Записей нет</p>}
            <ul className="list">
              {(notes.data?.items ?? []).map((n) => (
                <li key={n.id} className={`sev-${n.severity}`}>
                  <strong>{NOTE_KIND[n.kind] ?? n.kind}</strong> · {n.author?.display_name} · {fmtDate(n.created_at)}
                  <div>{n.body}</div>
                </li>
              ))}
            </ul>
            {can("order.note.write") && (o.coordinator_id === me?.id || can("order.read_all")) && (
              <NoteForm onSubmit={(n) => act("notes", n, "Запись добавлена", false)} />
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

const REPEATABLE = ["in_progress", "awaiting_delivery_confirmation", "admin_review", "completed", "disputed"];
const REPEAT_STATUS: Record<RepeatRequest["status"], [string, string]> = {
  pending: ["ждёт решения координатора", "warn"], approved: ["согласован", "ok"], rejected: ["отклонён", "danger"],
};

/** Повторный вылет: кнопка заказчика и запросы с решениями координатора. */
function RepeatCard({ o, busy, canRequest, canDecide, onRequest, onDecide }: {
  o: Order; busy: boolean; canRequest: boolean; canDecide: boolean;
  onRequest: (comment: string) => void; onDecide: (id: number, approve: boolean, comment: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const reqs = o.repeat_requests ?? [];
  return (
    <Card title="🔁 Повторный вылет">
      {canRequest && (open ? (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); onRequest(comment); setOpen(false); setComment(""); }}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} autoFocus
                 placeholder="Комментарий координатору (необязательно)" aria-label="Комментарий к повторному вылету" />
          <button className="btn primary" disabled={busy}>Отправить запрос</button>
          <button type="button" className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
        </form>
      ) : (
        <div className="row wrap">
          <button className="btn primary" disabled={busy} onClick={() => setOpen(true)}>Повторный вылет</button>
          <span className="muted small">Запрос уйдёт координатору заказа; после согласования будет создан новый приоритетный заказ
            с тем же маршрутом и грузом. Число запросов не ограничено.</span>
        </div>
      ))}
      {reqs.length > 0 && (
        <ul className="repeat-list">
          {reqs.map((r) => {
            const [label, tone] = REPEAT_STATUS[r.status];
            return (
              <li key={r.id}>
                <div className="row wrap">
                  <Tone tone={tone}>{label}</Tone>
                  <span className="small">{r.requested_by?.display_name ?? "—"}, {fmtDate(r.created_at)}</span>
                  {r.new_order && <Link to={`/orders/${r.new_order.id}`}>новый заказ №{r.new_order.number}</Link>}
                </div>
                {r.comment && <div className="small">«{r.comment}»</div>}
                {r.decided_by && <div className="muted small">Решение: {r.decided_by.display_name}, {fmtDate(r.decided_at)}
                  {r.decision_comment && <> — {r.decision_comment}</>}</div>}
                {canDecide && r.status === "pending" && (
                  <div className="row wrap">
                    <input value={answers[r.id] ?? ""} onChange={(e) => setAnswers({ ...answers, [r.id]: e.target.value })}
                           placeholder="Комментарий (для отказа — обязателен)" aria-label="Комментарий к решению" maxLength={2000} />
                    <button className="btn primary" disabled={busy} onClick={() => onDecide(r.id, true, answers[r.id] ?? "")}>Согласовать</button>
                    <button className="btn danger" disabled={busy || (answers[r.id] ?? "").trim().length < 3}
                            onClick={() => onDecide(r.id, false, answers[r.id] ?? "")}>Отклонить</button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

const DISPUTE_STATUS: Record<Complaint["status"], [string, "warn" | "ok" | "muted"]> = {
  open: ["на рассмотрении", "warn"], upheld: ["удовлетворена", "ok"], dismissed: ["отклонена", "muted"],
};

/** Претензия по заказу. Координатор заказа рассматривает её первым: «Отклонить» закрывает спор,
 *  «Удовлетворить» передаёт претензию администратору — окончательное решение и баллы за ним. */
function DisputeCard({ d, canReview, busy, onReview }: {
  d: Complaint; canReview: boolean; busy: boolean; onReview: (upheld: boolean, comment: string) => void;
}) {
  const [label, tone] = DISPUTE_STATUS[d.status];
  const awaitingCoordinator = d.status === "open" && !d.coordinator_verdict;
  return (
    <Card title="⚖ Претензия заказчика" className={d.status === "open" ? "dispute-card open" : "dispute-card"}>
      <KV items={[
        ["Статус", <Tone tone={tone}>{label}{d.status === "open" && (awaitingCoordinator
          ? " — у координатора" : " — у администратора")}</Tone>],
        ["Подана", `${d.author?.display_name ?? "—"}, ${fmtDate(d.created_at)}`],
        ["Суть претензии", d.description],
        ["Решение координатора", d.coordinator_verdict
          ? <><Tone tone={d.coordinator_verdict === "upheld" ? "ok" : "muted"}>
              {d.coordinator_verdict === "upheld" ? "удовлетворить" : "отклонить"}</Tone>
              {" "}— {d.coordinator_comment} <span className="muted small">({d.coordinator?.display_name}, {fmtDate(d.coordinator_at)})</span></>
          : "—"],
        ["Окончательное решение", d.status !== "open" ? `${d.resolution ?? ""}${d.points_adjustment != null ? ` (${d.points_adjustment} б.)` : ""}` : "—"],
      ]} />
      {canReview && awaitingCoordinator && (
        <div className="actions">
          <ReasonAction label="Удовлетворить претензию" busy={busy} required minLength={5}
                        placeholder="Обоснование — увидит администратор"
                        onSubmit={(comment) => onReview(true, comment)} />
          <ReasonAction label="Отклонить претензию" busy={busy} required danger minLength={5}
                        placeholder="Причина отклонения — увидит заказчик"
                        onSubmit={(comment) => onReview(false, comment)} />
          <p className="muted small">Удовлетворённая претензия передаётся администратору (ему приходит уведомление) —
            окончательное решение и корректировку баллов принимает он. Отклонение закрывает спор.</p>
        </div>
      )}
    </Card>
  );
}

function ReasonAction({ label, onSubmit, busy, required, danger, minLength = 3, placeholder = "Причина / комментарий" }: {
  label: string; onSubmit: (reason: string) => void; busy: boolean; required?: boolean; danger?: boolean; minLength?: number;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  if (!open) return <button className={`btn ${danger ? "danger" : ""}`} onClick={() => setOpen(true)}>{label}</button>;
  return (
    <form className="inline-form" onSubmit={(e) => { e.preventDefault(); onSubmit(text); setOpen(false); setText(""); }}>
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder}
             required={required} minLength={required ? minLength : undefined} autoFocus />
      <button className={`btn ${danger ? "danger" : "primary"}`} disabled={busy}>{label}</button>
      <button type="button" className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
    </form>
  );
}

function ReportForm({ onSubmit, busy }: { onSubmit: (r: Record<string, string>) => void; busy: boolean }) {
  const [r, setR] = useState({ summary: "", handed_over_to: "", remarks: "" });
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); onSubmit(r); }}>
      <strong>Отчёт о выполнении</strong>
      <label>Итог<textarea value={r.summary} onChange={(e) => setR({ ...r, summary: e.target.value })} required minLength={3} /></label>
      <label>Кому передан груз<input value={r.handed_over_to} onChange={(e) => setR({ ...r, handed_over_to: e.target.value })} /></label>
      <label>Замечания<textarea value={r.remarks} onChange={(e) => setR({ ...r, remarks: e.target.value })} /></label>
      <button className="btn primary" disabled={busy}>Сформировать отчёт</button>
    </form>
  );
}

function NoteForm({ onSubmit }: { onSubmit: (n: { kind: string; severity: string; body: string }) => void }) {
  const [n, setN] = useState({ kind: "remark", severity: "info", body: "" });
  return (
    <form className="form" onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(n); setN({ ...n, body: "" }); }}>
      <div className="row">
        <select value={n.kind} onChange={(e) => setN({ ...n, kind: e.target.value })}>
          {Object.entries(NOTE_KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select value={n.severity} onChange={(e) => setN({ ...n, severity: e.target.value })}>
          <option value="info">Информация</option><option value="warning">Предупреждение</option><option value="critical">Критично</option>
        </select>
      </div>
      <textarea value={n.body} onChange={(e) => setN({ ...n, body: e.target.value })} required placeholder="Текст записи" />
      <button className="btn primary">Добавить</button>
    </form>
  );
}

function AssignCoordinator({ current, onAssign }: { current: string | null; onAssign: (id: string) => void }) {
  const list = useLive(() => api.get<{ items: UserBrief[] }>("/api/v1/admin/coordinators"), []);
  const [sel, setSel] = useState(current ?? "");
  return (
    <div className="inline-form">
      <select value={sel} onChange={(e) => setSel(e.target.value)}>
        <option value="">— координатор —</option>
        {(list.data?.items ?? []).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
      </select>
      <button className="btn" disabled={!sel || sel === current} onClick={() => onAssign(sel)}>Назначить координатора</button>
    </div>
  );
}

export function VideoPlayer({ id, hasPreview }: { id: string; hasPreview: boolean }) {
  const [show, setShow] = useState(false);
  return (
    <div className="video">
      {show ? (
        hasPreview ? <video controls src={`/api/v1/videos/${id}/content?variant=preview`} />
          : <p className="muted small">Превью недоступно (на сервере нет ffmpeg). Скачайте оригинал.</p>
      ) : <button className="btn" onClick={() => setShow(true)}>Просмотреть клип (просмотр фиксируется)</button>}
      <a className="btn ghost" href={`/api/v1/videos/${id}/content?variant=original`}>Скачать оригинал (MPEG-TS)</a>
    </div>
  );
}

function ReviewPanel({ order, busy, onDone, setMsg }: {
  order: Order; busy: boolean; onDone: () => void; setMsg: (m: { tone: string; text: string } | null) => void;
}) {
  const [comment, setComment] = useState("");
  const [video, setVideo] = useState<string>(order.video?.status === "stored" ? "approve" : "skip");
  async function decide(approve: boolean) {
    try {
      await api.post(`/api/v1/orders/${order.id}/review`, {
        approve, comment, expected_version: order.version,
        video_verified: video === "skip" ? null : video === "approve",
      }, { idempotencyKey: newIdempotencyKey() });
      setMsg({ tone: "ok", text: approve ? "Выполнение подтверждено, баллы начислены" : "Отчёт отклонён" });
      onDone();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }
  return (
    <div className="review">
      <strong>Проверка выполнения</strong>
      <label>Видеоподтверждение
        <select value={video} onChange={(e) => setVideo(e.target.value)}>
          {order.video?.status === "stored" && <option value="approve">Видео подтверждает доставку</option>}
          {order.video?.status === "stored" && <option value="reject">Видео не подтверждает доставку</option>}
          <option value="skip">Видео не оценивается</option>
        </select>
      </label>
      <label>Комментарий (обязателен при отклонении)<textarea value={comment} onChange={(e) => setComment(e.target.value)} /></label>
      <div className="row">
        <button className="btn primary" disabled={busy} onClick={() => decide(true)}>Подтвердить и начислить баллы</button>
        <button className="btn danger" disabled={busy || !comment} onClick={() => decide(false)}>Отклонить</button>
      </div>
    </div>
  );
}
