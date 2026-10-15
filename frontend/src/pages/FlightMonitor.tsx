import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router";
import { api, errorText, newIdempotencyKey } from "../api";
import { useAuth } from "../auth";
import { LiveVideo } from "../components/LiveVideo";
import { RouteMap } from "../components/RouteMap";
import { CARGO_TYPES, ORDER_STATUS } from "../labels";
import { useLive } from "../realtime";
import type { Note, Order, Point, Telemetry, TelemetryExtra } from "../types";

/* Монитор полёта — отдельное окно оператора (и назначенного координатора, администратора).
 * Только отображение: видео с камеры, телеметрия, маршрут, переписка. Команды управления полётом
 * платформа не передаёт (ТЗ: контур телеметрии отделён от управления) — режимы полёта показываются
 * как индикаторы, управление выполняется штатным пультом дрона. */

const PHASES: Record<string, string> = {
  idle: "На земле", preflight: "Предполётная подготовка", takeoff: "Взлёт", cruise: "В полёте",
  approach: "Заход на точку Б", delivery: "Доставка", return: "Возврат", landed: "Посадка выполнена",
};
const MODES: { key: string; label: string; icon: string }[] = [
  { key: "manual", label: "Ручной", icon: "🎮" },
  { key: "auto", label: "Авто", icon: "📍" },
  { key: "return", label: "Возврат", icon: "🏠" },
];
const STALE_MS = 5000;
const PREFLIGHT = ["accepted", "awaiting_readiness", "ready"];

const fmt = (v: number | null | undefined, digits = 0, unit = "") =>
  v == null || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}${unit}`;

function distM(a: Point, b: Point): number {
  const k = Math.cos(((a.lat + b.lat) / 2) * (Math.PI / 180));
  return Math.hypot((b.lat - a.lat) * 111320, (b.lon - a.lon) * 111320 * k);
}

function duration(ms: number): string {
  if (ms < 0) return "00:00:00";
  const s = Math.floor(ms / 1000);
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((n) => String(n).padStart(2, "0")).join(":");
}

function linkLabel(q: number | null | undefined, stale: boolean): [string, string] {
  if (stale || q == null || q === 0) return ["Нет связи", "bad"];
  if (q >= 85) return ["Отличная", "ok"];
  if (q >= 60) return ["Хорошая", "ok"];
  return ["Слабая", "warn"];
}

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function FlightMonitorPage() {
  const { orderId = "" } = useParams();
  const { me, can } = useAuth();
  const now = useNow();
  const order = useLive(() => api.get<Order>(`/api/v1/orders/${orderId}`), [orderId],
    (m) => m.data.order_id === orderId && m.kind !== "telemetry");
  const telemetry = useLive(
    () => api.get<{ items: Telemetry[] }>(`/api/v1/orders/${orderId}/telemetry?limit=600`), [orderId],
    (m) => m.kind === "telemetry" && m.data.order_id === orderId);
  // Резервный опрос на случай пропуска сигналов реального времени.
  useEffect(() => {
    const t = window.setInterval(() => telemetry.reload(), 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const o = order.data;
  useEffect(() => {
    if (o) document.title = `Монитор полёта — заказ №${o.number}`;
  }, [o]);

  if (order.error) return <div className="monitor monitor-msg">{errorText(order.error)}</div>;
  if (!o) return <div className="monitor monitor-msg">Загрузка…</div>;

  const track = telemetry.data?.items ?? [];
  const last = track[track.length - 1];
  const x: TelemetryExtra = last?.extra ?? {};
  // Небольшое расхождение часов дрона и рабочего места не должно давать отрицательный возраст данных.
  const age = last ? Math.max(0, now - new Date(last.recorded_at).getTime()) : Infinity;
  const stale = age > STALE_MS;
  const flying = o.status === "in_progress";
  // До начала задания телеметрия к заказу не привязана — показывается состояние связи самого дрона.
  const [linkText, linkTone] = flying ? linkLabel(last?.link_quality, stale)
    : o.drone?.link_status === "online" ? ["На связи", "ok"]
    : o.drone?.link_status === "degraded" ? ["Слабая", "warn"] : ["Нет связи", "bad"];
  const battery = last?.battery_pct ?? o.drone?.battery_pct ?? null;
  // После завершения доставки время полёта фиксируется на моменте завершения.
  const flightEnd = flying ? now : o.delivered_at ? new Date(o.delivered_at).getTime() : now;
  const flightMs = o.started_at ? flightEnd - new Date(o.started_at).getTime() : 0;
  const route = o.route;
  const wpTotal = x.waypoint_total ?? (route ? route.checkpoints.length + 1 : 0);
  const wpIndex = x.waypoint_index ?? 0;
  const remaining = route && last?.lat != null && last?.lon != null
    ? remainingDistance({ lat: last.lat, lon: last.lon }, route.checkpoints, route.destination, wpIndex)
    : null;
  const eta = remaining != null && last?.ground_speed_mps ? remaining / last.ground_speed_mps * 1000 : null;
  const mode = !flying ? "" : last?.flight_phase === "return" ? "return" : x.flight_mode ?? "auto";
  const warnings: string[] = [];
  if (flying && stale) warnings.push(last ? `Нет телеметрии ${Math.round(age / 1000)} с` : "Телеметрия не поступает");
  if (battery != null && battery < 30) warnings.push(`Низкий заряд батареи: ${battery}%`);
  if (last && last.gnss_fix === false) warnings.push("Нет спутникового решения (GNSS)");
  const canChat = can("order.chat") && (o.operator_id === me?.id || o.coordinator_id === me?.id || can("order.read_all"));

  return (
    <div className="monitor">
      <header className="mon-top">
        <div className="mon-drone">
          <span className="mon-drone-icon" aria-hidden>✣</span>
          <div>
            <div className="mon-title">{o.drone ? `${o.drone.model} ${o.drone.registration_number}` : "Дрон"}</div>
            <div className="mon-sub">Заказ №{o.number} · {CARGO_TYPES[o.cargo_type] ?? o.cargo_type}</div>
          </div>
        </div>
        <div className="mon-stat">
          <span className={`dot ${flying && !stale ? "ok" : flying ? "bad" : "idle"}`} />
          <div><b>{flying ? PHASES[last?.flight_phase ?? ""] ?? "Выполняется" : ORDER_STATUS[o.status]?.label ?? o.status_label}</b>
            <small>{!flying ? "задание не выполняется" : mode === "auto" ? "Автономный режим" : mode === "return" ? "Возврат" : "Ручной режим"}</small></div>
        </div>
        <div className="mon-stat">
          <span className="mon-ico">📶</span>
          <div><b>GPS</b><small>{last?.gnss_fix === false ? "нет решения" : x.satellites != null ? `${x.satellites} спутников` : last ? "есть решение" : "—"}</small></div>
        </div>
        <div className="mon-stat">
          <span className={`mon-batt ${battery != null && battery < 30 ? "low" : ""}`}><i style={{ height: `${battery ?? 0}%` }} /></span>
          <div><b>{fmt(battery, 0, "%")}</b><small>{x.voltage_v != null ? `${x.voltage_v.toFixed(1)} В` : "заряд"}</small></div>
        </div>
        <div className="mon-stat">
          <span className="mon-ico">🔗</span>
          <div><b>Связь</b><small className={`mon-tone-${linkTone}`}>{linkText}{flying && last?.link_quality != null && !stale ? ` (${last.link_quality})` : ""}</small></div>
        </div>
        {flying && o.operator_id === me?.id && can("order.execute") && (
          <FinishButton order={o} onDone={() => order.reload()} />
        )}
        <div className="mon-actions">
          <button className="mon-btn" title="Во весь экран" onClick={() =>
            document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()}>⛶</button>
          <a className="mon-btn" href={`/orders/${o.id}`} target="fpv-main" title="Карточка заказа">☰</a>
        </div>
      </header>

      <section className="mon-video">
        {flying ? (
          <LiveVideo key={o.id} orderId={o.id} variant="hud" autoStart>
            <Hud last={last} extra={x} maxAlt={route?.max_altitude_m ?? 120} flightMs={flightMs} age={age} />
          </LiveVideo>
        ) : (
          <div className="live-hud"><div className="live-hud-idle">
            {PREFLIGHT.includes(o.status)
              ? o.operator_id === me?.id && can("order.execute")
                ? <TaskPanel order={o} onChanged={() => order.reload()} />
                : "Видео и телеметрия появятся после начала выполнения задания"
              : o.status === "awaiting_delivery_confirmation"
                ? <div className="mon-task"><div className="mon-task-title mon-tone-ok">✓ Доставка зафиксирована</div>
                    <div>Видеоподтверждение (последние 15 секунд) сохраняется. Ожидается подтверждение получения
                      заказчиком; отчёт о выполнении формируется в карточке заказа.</div></div>
                : `Заказ: ${ORDER_STATUS[o.status]?.label ?? o.status}. Трансляция не ведётся.`}
          </div></div>
        )}
        {warnings.length > 0 && <div className="mon-warn">{warnings.map((w) => <div key={w}>⚠ {w}</div>)}</div>}
      </section>

      <section className="mon-panel mon-map">
        <div className="mon-panel-head"><span>🗺 Карта маршрута</span></div>
        {route ? (
          <div className="mon-map-body">
            <RouteMap route={route} track={track} height={400} compact passed={Math.max(0, wpIndex - 1)} />
            <div className="mon-map-info">
              <div>Точка {wpIndex || "—"} / {wpTotal || "—"}</div>
              <div>До точки Б: {remaining != null ? `${(remaining / 1000).toFixed(2)} км` : "—"}</div>
              <div>Высота: {fmt(last?.alt_m, 0, " м")}</div>
              <div>В полёте: {o.started_at ? duration(flightMs) : "—"}</div>
              {eta != null && flying && <div>Прибытие через: {duration(eta)}</div>}
            </div>
          </div>
        ) : <p className="mon-muted">Маршрут не рассчитан</p>}
      </section>

      <Chat order={o} canWrite={canChat} flying={flying} />

      <section className="mon-bottom">
        <div className="mon-panel">
          <div className="mon-panel-head"><span>Режим полёта</span><small>только отображение</small></div>
          <div className="mon-modes">
            {MODES.map((m) => (
              <div key={m.key} className={`mon-mode ${mode === m.key ? "active" : ""}`} aria-current={mode === m.key}>
                <span>{m.icon}</span>{m.label}
              </div>
            ))}
          </div>
          <small className="mon-muted">Управление — штатным пультом дрона; платформа команд не передаёт.</small>
        </div>
        <div className="mon-panel">
          <div className="mon-panel-head"><span>Параметры полёта</span></div>
          <div className="mon-kv">
            <div><small>Высота (предел)</small><b>{route ? `${route.max_altitude_m} м` : "—"}</b></div>
            <div><small>Скорость</small><b>{fmt(last?.ground_speed_mps, 1, " м/с")}</b></div>
            <div><small>Маршрут</small><b><span className={`dot ${route?.approval_status === "approved" ? "ok" : "warn"}`} />
              {route?.approval_status === "approved" ? "Согласован" : "Не согласован"}</b></div>
          </div>
        </div>
        <div className="mon-panel">
          <div className="mon-panel-head"><span>Полезная нагрузка</span></div>
          <div className="mon-kv">
            <div><small>Груз</small><b>📦 {o.cargo_weight_g} г</b></div>
            <div><small>Тип</small><b>{CARGO_TYPES[o.cargo_type] ?? o.cargo_type}</b></div>
            <div><small>Статус</small><b><span className={`dot ${flying ? "ok" : "idle"}`} />{flying ? "В пути" : ORDER_STATUS[o.status]?.label}</b></div>
          </div>
        </div>
        <div className="mon-panel mon-drone-card">
          <span className="mon-drone-big" aria-hidden>✣</span>
          <b>{o.drone?.registration_number ?? "—"}</b>
          <small><span className={`dot ${o.drone?.link_status === "online" ? "ok" : "bad"}`} />{o.drone?.link_status === "online" ? "на связи" : "нет связи"}</small>
        </div>
      </section>
    </div>
  );
}

/** Оставшееся расстояние по маршруту: до текущей контрольной точки и далее по оставшимся до точки Б. */
function remainingDistance(pos: Point, checkpoints: Point[], dest: Point, wpIndex: number): number {
  const rest = [...checkpoints, dest].slice(Math.max(0, wpIndex - 1));
  let d = 0;
  let prev = pos;
  for (const p of rest) {
    d += distM(prev, p);
    prev = p;
  }
  return d;
}

function Hud({ last, extra, maxAlt, flightMs, age }: {
  last?: Telemetry; extra: TelemetryExtra; maxAlt: number; flightMs: number; age: number;
}) {
  const heading = last?.heading_deg ?? null;
  const alt = last?.alt_m ?? null;
  const vs = extra.vspeed_mps ?? null;
  const altTop = Math.max(50, Math.ceil((maxAlt * 1.25) / 50) * 50);
  const altStep = [10, 25, 50, 100, 200].find((st) => altTop / st <= 5) ?? 250;
  const altTicks = Array.from({ length: Math.floor(altTop / altStep) + 1 }, (_, i) => i * altStep);
  const altPos = alt == null ? null : Math.min(100, Math.max(0, (alt / altTop) * 100));
  const vsPos = vs == null ? null : Math.min(100, Math.max(0, 50 - (vs / 5) * 50));
  const tape = useMemo(() => {
    if (heading == null) return [];
    const marks = [];
    for (let d = -60; d <= 60; d += 10) {
      const deg = Math.round(((heading + d) % 360 + 360) % 360 / 10) * 10 % 360;
      const off = ((deg - heading + 540) % 360) - 180;
      marks.push({ off, label: ({ 0: "N", 90: "E", 180: "S", 270: "W" } as Record<number, string>)[deg] ?? (deg % 30 === 0 ? String(deg) : "") });
    }
    return marks;
  }, [heading]);
  return (
    <div className="hud" aria-label="Телеметрия">
      <div className="hud-top">
        <div className="hud-cell"><small>Высота (AGL)</small><b>{fmt(alt, 0, " м")}</b></div>
        <div className="hud-cell"><small>Высота (MSL)</small><b>{fmt(extra.alt_msl_m, 0, " м")}</b></div>
        <div className="hud-cell"><small>Скорость</small><b>{fmt(last?.ground_speed_mps, 1, " м/с")}</b></div>
        <div className="hud-cell"><small>Ветер</small><b>{fmt(extra.wind_mps, 1, " м/с")}
          {extra.wind_dir_deg != null && <span className="hud-wind" style={{ transform: `rotate(${extra.wind_dir_deg + 180}deg)` }}>↑</span>}</b></div>
        <div className="hud-tape">
          {tape.map((m, i) => <span key={i} style={{ left: `${50 + m.off * 0.75}%` }}>{m.label || "·"}</span>)}
          <div className="hud-tape-mark">▼</div>
          <div className="hud-heading">{heading == null ? "—" : `${Math.round(heading)}°`}</div>
        </div>
        <div className="hud-coords">{last?.lat != null ? `${last.lat.toFixed(4)}° N` : "—"}<br />{last?.lon != null ? `${last.lon.toFixed(4)}° E` : ""}</div>
      </div>
      <div className="hud-alt-scale">
        {altTicks.map((v) => <span key={v} style={{ top: `${(1 - v / altTop) * 100}%` }}>{v}</span>)}
        {altPos != null && <div className="hud-alt-mark" style={{ bottom: `${altPos}%` }}>{Math.round(alt!)}</div>}
      </div>
      <div className="hud-vs-scale">
        {["+5", "0", "−5"].map((l, i) => <span key={l} style={{ top: `${i * 50}%` }}>{l}</span>)}
        {vsPos != null && <div className="hud-vs-mark" style={{ top: `${vsPos}%` }}>{vs! > 0 ? "+" : ""}{vs!.toFixed(1)} м/с</div>}
      </div>
      <div className="hud-cross" aria-hidden><i /><i /><i /><i /></div>
      <div className="hud-bottom-left">
        <Attitude heading={heading} pitch={extra.pitch_deg ?? 0} roll={extra.roll_deg ?? 0} />
        <div className="hud-flight">
          <div>Режим полёта: <b>{extra.flight_mode === "auto" ? "Авто (по маршруту)" : extra.flight_mode === "manual" ? "Ручной" : last?.flight_phase === "return" ? "Возврат" : "—"}</b></div>
          <div>Заряд батареи: <b>{fmt(last?.battery_pct, 0, "%")}{extra.voltage_v != null ? ` (${extra.voltage_v.toFixed(1)} В)` : ""}</b></div>
          <div>Время полёта: <b>{flightMs > 0 ? duration(flightMs) : "—"}</b></div>
        </div>
      </div>
      <div className="hud-bottom-right">
        {extra.video_resolution && <div>📷 {extra.video_resolution}{extra.video_fps ? ` · ${extra.video_fps} к/с` : ""}</div>}
        <div>Данные: {Number.isFinite(age) ? `${(age / 1000).toFixed(1)} с назад` : "нет"}</div>
      </div>
    </div>
  );
}

function Attitude({ heading, pitch, roll }: { heading: number | null; pitch: number; roll: number }) {
  return (
    <svg className="hud-att" viewBox="-60 -60 120 120" role="img" aria-label="Пространственное положение и курс">
      <defs><clipPath id="att-clip"><circle r="40" /></clipPath></defs>
      <g clipPath="url(#att-clip)">
        <g transform={`rotate(${-roll}) translate(0 ${pitch * 1.5})`}>
          <rect x="-80" y="-120" width="160" height="120" className="att-sky" />
          <rect x="-80" y="0" width="160" height="120" className="att-ground" />
          <line x1="-80" x2="80" y1="0" y2="0" className="att-line" />
          {[-10, 10].map((p) => <line key={p} x1="-10" x2="10" y1={-p * 1.5} y2={-p * 1.5} className="att-line thin" />)}
        </g>
      </g>
      <path d="M-18 0 H-6 L0 5 L6 0 H18" className="att-plane" />
      <circle r="40" className="att-ring" />
      <g transform={`rotate(${-(heading ?? 0)})`}>
        {["N", "E", "S", "W"].map((l, i) => (
          <text key={l} transform={`rotate(${i * 90}) translate(0 -47) rotate(${-i * 90 + (heading ?? 0)})`} className="att-card">{l}</text>
        ))}
      </g>
      <path d="M0 -58 L4 -52 H-4 Z" className="att-ptr" />
    </svg>
  );
}

const NOTE_LABEL: Record<string, string> = {
  remark: "Замечание", message_to_operator: "Сообщение оператору", incident: "Инцидент", deviation: "Отклонение",
};

function Chat({ order, canWrite, flying }: { order: Order; canWrite: boolean; flying: boolean }) {
  const { me } = useAuth();
  const notes = useLive(() => api.get<{ items: Note[] }>(`/api/v1/orders/${order.id}/notes`), [order.id],
    (m) => m.kind === "order.note_added" && m.data.order_id === order.id);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = useRef<HTMLDivElement>(null);
  const items = notes.data?.items ?? [];
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [items.length]);

  async function send(e: FormEvent | null, attachFrame = false) {
    e?.preventDefault();
    const body = text.trim() || (attachFrame ? "Кадр с камеры" : "");
    if (!body) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/orders/${order.id}/chat`, { body, attach_frame: attachFrame });
      setText("");
      notes.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const peer = order.operator_id === me?.id ? "координатором" : "оператором";
  return (
    <section className="mon-panel mon-chat">
      <div className="mon-panel-head"><span>💬 Связь с {peer}</span>
        <small><span className={`dot ${order.coordinator_id ? "ok" : "idle"}`} />{order.coordinator_id ? order.coordinator?.display_name : "координатор не назначен"}</small></div>
      <div className="mon-chat-list" ref={list}>
        {notes.error ? <p className="mon-muted">{errorText(notes.error)}</p> : items.length === 0 && <p className="mon-muted">Сообщений нет</p>}
        {items.map((n) => {
          const mine = n.author?.id === me?.id;
          return (
            <div key={n.id} className={`mon-msg ${mine ? "mine" : ""} sev-${n.severity}`}>
              <div className="mon-msg-head">{mine ? "Вы" : n.author?.display_name ?? "—"}
                {n.kind !== "chat" && <em> · {NOTE_LABEL[n.kind] ?? n.kind}</em>}
                <time>{new Date(n.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time></div>
              <div className="mon-msg-body">{n.body}</div>
              {n.attachment_url && <a href={n.attachment_url} target="_blank" rel="noreferrer"><img src={n.attachment_url} alt="Кадр с камеры дрона" /></a>}
            </div>
          );
        })}
      </div>
      {canWrite ? (
        <form className="mon-chat-form" onSubmit={(e) => send(e)}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Написать сообщение…" maxLength={2000} disabled={busy} />
          <button type="button" className="mon-btn" title="Отправить кадр с камеры" disabled={busy || !flying}
                  onClick={() => send(null, true)}>📷</button>
          <button className="mon-btn primary" title="Отправить" disabled={busy || !text.trim()}>➤</button>
        </form>
      ) : <small className="mon-muted">Переписка доступна назначенным оператору и координатору.</small>}
      {error && <small className="mon-tone-bad">{error}</small>}
    </section>
  );
}

/** Подготовка к выполнению задания (оператор): проверка готовности и начало задания.
 *  Те же действия, что в карточке заказа; сервер повторно проверяет права и допустимость перехода. */
function TaskPanel({ order, onChanged }: { order: Order; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ready: boolean; reasons: string[] } | null>(null);
  const [error, setError] = useState("");

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const check = () => run(async () => {
    const r = await api.post<{ ready: boolean; reasons: string[] }>(`/api/v1/orders/${order.id}/readiness-check`);
    setResult({ ready: r.ready, reasons: r.reasons });
  });
  const start = () => run(async () => {
    await api.post(`/api/v1/orders/${order.id}/start`, { expected_version: order.version },
      { idempotencyKey: newIdempotencyKey() });
  });

  const ready = order.status === "ready";
  return (
    <div className="mon-task">
      <div className="mon-task-title">Задание №{order.number}: {ORDER_STATUS[order.status]?.label ?? order.status}</div>
      <ol className="mon-task-steps">
        <li className={ready ? "done" : "current"}>Проверка готовности дрона и маршрута</li>
        <li className={ready ? "current" : ""}>Начало выполнения задания</li>
      </ol>
      {result && !result.ready && (
        <ul className="mon-task-reasons">{result.reasons.map((r) => <li key={r}>✕ {r}</li>)}</ul>
      )}
      {ready && <div className="mon-task-ok">✓ Проверка готовности пройдена</div>}
      <div className="mon-task-actions">
        <button className="mon-btn" disabled={busy} onClick={check}>{busy ? "…" : "Проверить готовность"}</button>
        {ready && <button className="mon-btn primary" disabled={busy} onClick={start}>▶ Начать задание</button>}
      </div>
      {error && <div className="mon-tone-bad">{error}</div>}
      <small>Видео и телеметрия появятся после начала выполнения задания.</small>
    </div>
  );
}

/** Фиксация завершения доставки (оператор). То же действие, что в карточке заказа:
 *  сохраняются последние 15 секунд видео, заказ переходит к подтверждению получения. */
function FinishButton({ order, onDone }: { order: Order; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function finish() {
    if (!confirm("Зафиксировать завершение доставки? Будут сохранены последние 15 секунд видео.")) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/orders/${order.id}/complete`, {}, { idempotencyKey: newIdempotencyKey() });
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mon-finish">
      <button className="mon-btn finish" disabled={busy} onClick={finish}>{busy ? "Фиксация…" : "✓ Завершить доставку"}</button>
      {error && <small className="mon-tone-bad">{error}</small>}
    </div>
  );
}
