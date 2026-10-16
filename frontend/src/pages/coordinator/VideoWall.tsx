import { useEffect } from "react";
import { Link } from "react-router";
import { api, errorText } from "../../api";
import { LiveVideo } from "../../components/LiveVideo";
import { Alert, Badge, Pending } from "../../components/ui";
import { useLive } from "../../realtime";
import type { Order, Telemetry } from "../../types";
import { openFlightMonitor } from "../OrderDetail";

interface WallItem extends Order {
  drone: { registration_number: string | null; link_status: string; battery_pct: number | null } & Order["drone"];
  telemetry: Telemetry | null;
}

const PHASES: Record<string, string> = {
  idle: "На земле", preflight: "Подготовка", takeoff: "Взлёт", cruise: "В полёте", approach: "Заход",
  pickup: "Забор груза", delivery: "Доставка", return: "Возврат", landed: "Посадка",
};
const STALE_MS = 10000;

/** Видеостена координатора: до 9 сопровождаемых заказов в одном окне (кадры раз в секунду,
 *  чтобы не перегружать канал и браузер); любую плитку можно открыть монитором полёта в новом окне. */
export function VideoWallPage() {
  const wall = useLive(() => api.get<{ items: WallItem[]; load: number; capacity: number; total: number }>(
    "/api/v1/coordinator/wall"), [], (m) => m.kind.startsWith("order.") || m.kind === "telemetry");
  useEffect(() => {                           // телеметрия на плитках — резервный опрос раз в 5 с
    const t = window.setInterval(() => wall.reload(), 5000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (wall.error) return <Alert>{errorText(wall.error)}</Alert>;
  if (!wall.data) return <Pending />;
  const { items, load, capacity, total } = wall.data;
  const slots = Array.from({ length: capacity }, (_, i) => items[i] ?? null);
  return (
    <div className="stack">
      <div className="row between">
        <h1>Видеостена</h1>
        <span className="muted">Сопровождаю: <b>{load}</b> из {capacity} · заказы назначаются автоматически с учётом нагрузки</span>
      </div>
      {total > capacity && <Alert tone="warn">Назначено больше заказов, чем помещается на стене ({total}) — остальные в «Назначенных заказах».</Alert>}
      <div className="wall">
        {slots.map((o, i) => o ? <WallTile key={o.id} o={o} /> : (
          <div key={`empty-${i}`} className="wall-tile empty"><span>Свободно</span></div>
        ))}
      </div>
    </div>
  );
}

function WallTile({ o }: { o: WallItem }) {
  const t = o.telemetry;
  const age = t ? Date.now() - new Date(t.recorded_at).getTime() : Infinity;
  const flying = o.status === "in_progress";
  const alerts: string[] = [];
  if (flying && age > STALE_MS) alerts.push(t ? `нет телеметрии ${Math.round(age / 1000)} с` : "телеметрия не поступает");
  if (flying && o.drone && ["lost", "degraded"].includes(o.drone.link_status)) alerts.push("нарушена связь");
  const battery = t?.battery_pct ?? o.drone?.battery_pct ?? null;
  if (battery != null && battery < 30) alerts.push(`заряд ${battery}%`);
  return (
    <div className={`wall-tile ${alerts.length ? "alert" : ""}`}>
      {flying ? (
        <LiveVideo key={o.id} orderId={o.id} variant="hud" autoStart initialMode="frames">
          <TileInfo o={o} t={t} battery={battery} alerts={alerts} />
        </LiveVideo>
      ) : (
        <div className="live-hud">
          <TileInfo o={o} t={t} battery={battery} alerts={alerts} />
          <div className="live-hud-idle">{o.status === "awaiting_delivery_confirmation"
            ? "Доставка зафиксирована — ждём подтверждения получения" : "Задание ещё не начато — видео появится с вылетом"}</div>
        </div>
      )}
    </div>
  );
}

function TileInfo({ o, t, battery, alerts }: { o: WallItem; t: Telemetry | null; battery: number | null; alerts: string[] }) {
  return (
    <div className="wall-info">
      <div className="wall-head">
        <Link to={`/orders/${o.id}`} className="wall-num">№{o.number}</Link>
        <Badge kind="order" value={o.status} />
        <button className="btn small" onClick={() => openFlightMonitor(o.id)} title="Открыть монитор полёта в новом окне">⤢ Открыть</button>
      </div>
      <div className="wall-stats">
        <span>{o.drone?.registration_number ?? "—"}</span>
        <span>{t?.flight_phase ? PHASES[t.flight_phase] ?? t.flight_phase : "—"}</span>
        <span>{t?.alt_m != null ? `${Math.round(t.alt_m)} м` : "—"}</span>
        <span>{t?.ground_speed_mps != null ? `${t.ground_speed_mps.toFixed(1)} м/с` : "—"}</span>
        <span>🔋 {battery != null ? `${battery}%` : "—"}</span>
      </div>
      {alerts.length > 0 && <div className="wall-alert">⚠ {alerts.join(" · ")}</div>}
    </div>
  );
}
