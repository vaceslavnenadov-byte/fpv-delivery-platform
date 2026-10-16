import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { fmtDate } from "../labels";
import { useLive } from "../realtime";
import type { DroneConnection as Conn } from "../types";
import { LiveVideo } from "./LiveVideo";
import { Alert, Badge, Card, KV, Pending, Tone } from "./ui";

/** Подключение дрона к системе (техник парка): связь с сервисным модулем, телеметрия,
 *  источник видео и пробная трансляция до заказа. Управления полётом здесь нет. */
export function DroneConnection({ droneId }: { droneId: string }) {
  const conn = useLive(() => api.get<Conn>(`/api/v1/drones/${droneId}/connection`), [droneId],
    (m) => (m.kind === "drone.updated" || m.kind === "video_source") && m.data.drone_id === droneId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [choice, setChoice] = useState("");
  useEffect(() => {                         // телеметрия и связь меняются постоянно — опрос раз в 3 с
    const t = window.setInterval(() => conn.reload(), 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [droneId]);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      conn.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const c = conn.data;
  if (conn.error) return <Alert>{errorText(conn.error)}</Alert>;
  if (!c) return <Pending />;
  const t = c.telemetry;
  const v = c.video;
  const selected = choice || v.selected || v.active || "";
  const checkedRecently = v.checked_at && Date.now() - new Date(v.checked_at).getTime() < 12 * 3600 * 1000;

  return (
    <div className="grid2">
      <Card title="Связь и телеметрия">
        <KV items={[
          ["Связь с сервисным модулем", <Badge kind="link" value={c.link_status} />],
          ["Последний сеанс", fmtDate(c.last_seen_at)],
          ["Ключ модуля", c.has_key ? <Tone tone="ok">выпущен</Tone> : <Tone tone="warn">не выпущен</Tone>],
          ["Заряд", c.battery_pct != null ? `${c.battery_pct}%` : "—"],
          ["Телеметрия", t ? <>{t.age_s <= 30 ? <Tone tone="ok">поступает</Tone> : <Tone tone="danger">нет {Math.round(t.age_s)} с</Tone>}
            {" "}{t.lat != null ? `${t.lat.toFixed(5)}, ${t.lon?.toFixed(5)}` : ""}</> : <Tone tone="danger">не поступала</Tone>],
          ["GNSS / качество связи", t ? `${t.gnss_fix ? "есть решение" : "нет решения"} · ${t.link_quality ?? "—"}` : "—"],
          ["Фаза", t?.flight_phase ?? "—"],
        ]} />
        {!c.has_key && <p className="muted small">Выпустите ключ в разделе «Мой парк» и запишите его в /etc/fpv-agent/key на компьютере дрона.</p>}
      </Card>

      <Card title="Видео с дрона">
        {v.sources.length === 0 ? (
          <p className="muted">Сервисный модуль ещё не сообщил источники видео{c.link_status !== "online" ? " (дрон не на связи)" : ""}.</p>
        ) : (
          <fieldset className="vs-list" disabled={busy}>
            <legend className="small muted">Источник видео по умолчанию</legend>
            {v.sources.map((s) => (
              <label key={s.id} className="check">
                <input type="radio" name={`src-${droneId}`} checked={selected === s.id} onChange={() => setChoice(s.id)} />
                <span>{s.label}{s.kind === "usb" ? " · USB" : ""}</span>
                {v.active === s.id && <Tone tone="ok">в эфире</Tone>}
                {v.selected === s.id && v.active !== s.id && <Tone tone="muted">выбран</Tone>}
              </label>
            ))}
            <div className="row">
              <button className="btn small" disabled={busy || !selected || selected === v.selected}
                      onClick={() => run(() => api.put(`/api/v1/drones/${droneId}/video-source`, { source_id: selected }))}>
                Сделать источником по умолчанию
              </button>
            </div>
          </fieldset>
        )}
        <div className="row">
          {!v.check_active ? (
            <button className="btn primary" disabled={busy || !c.has_key}
                    onClick={() => run(() => api.post(`/api/v1/drones/${droneId}/video-check`))}>📹 Проверить видео</button>
          ) : (
            <button className="btn" disabled={busy}
                    onClick={() => run(() => api.del(`/api/v1/drones/${droneId}/video-check`))}>Остановить проверку</button>
          )}
          <span className="small muted">
            {checkedRecently ? <>✓ Видео проверено {fmtDate(v.checked_at)}</> : "Видео ещё не проверено"}
          </span>
        </div>
        {v.check_active && (
          <>
            <p className="muted small">Пробная трансляция идёт до {fmtDate(v.check_until)}; видео не сохраняется.
              {!v.receiving && " Ожидание видео от сервисного модуля…"}</p>
            <LiveVideo key={`check-${droneId}`} orderId={droneId} autoStart baseUrl={`/api/v1/drones/${droneId}/live/check`} />
          </>
        )}
        <Alert>{error}</Alert>
      </Card>
    </div>
  );
}
