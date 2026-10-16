import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../api";
import { useLive } from "../realtime";

export interface VideoSource { id: string; kind: "usb" | "rtsp" | "file" | "test"; label: string }
interface Info {
  sources: VideoSource[];
  active: string | null;
  selected: string | null;
  can_select: boolean;
  link_status: string;
}

const GROUPS: { kind: VideoSource["kind"]; title: string; hint: string }[] = [
  { kind: "usb", title: "USB-камеры", hint: "камеры и платы захвата, подключённые к сервисному модулю дрона" },
  { kind: "rtsp", title: "RTSP-потоки", hint: "IP-камеры из /etc/fpv-agent/cameras.conf на сервисном модуле" },
  { kind: "file", title: "Тестовые ролики", hint: "файлы из папки /var/lib/fpv-agent/videos, воспроизводятся по кругу" },
  { kind: "test", title: "Тестовая таблица", hint: "генерируется сервисным модулем" },
];

/** Окно выбора видеопотока дрона (монитор полёта). Список источников сообщает сервисный модуль
 *  дрона; оператор выбирает один из них, модуль переключает видео в течение нескольких секунд.
 *  Выбор относится только к видео — на полёт он не влияет. */
export function VideoSourceDialog({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const info = useLive(() => api.get<Info>(`/api/v1/orders/${orderId}/video-source`), [orderId],
    (m) => m.kind === "video_source" && (m.data.order_id === orderId || !m.data.order_id));
  const [choice, setChoice] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
  }, []);
  useEffect(() => {                         // переключение подтверждается модулем — опрос, пока окно открыто
    const t = window.setInterval(() => info.reload(), 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const d = info.data;
  useEffect(() => {
    if (d && !choice) setChoice(d.selected ?? d.active ?? "");
  }, [d, choice]);

  async function apply() {
    setBusy(true);
    setError("");
    try {
      await api.put(`/api/v1/orders/${orderId}/video-source`, { source_id: choice });
      info.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const pending = d?.selected && d.selected !== d.active;
  return (
    <dialog ref={dialog} className="mon-dialog" onClose={onClose} aria-labelledby="vs-title">
      <div className="mon-dialog-head">
        <b id="vs-title">⚙ Видеопоток дрона</b>
        <button className="mon-btn" onClick={() => dialog.current?.close()} aria-label="Закрыть">✕</button>
      </div>
      {info.error ? <p className="mon-tone-bad">{errorText(info.error)}</p> : !d ? <p className="mon-muted">Загрузка…</p> : (
        <>
          {d.sources.length === 0 && (
            <p className="mon-muted">Сервисный модуль дрона ещё не сообщил список источников
              {d.link_status !== "online" ? " (дрон не на связи)" : ""}. Видео идёт из источника по умолчанию;
              если список не появится в течение минуты — сообщите технику парка (журнал: journalctl -u fpv-agent).</p>
          )}
          {GROUPS.map((g) => {
            const items = d.sources.filter((s) => s.kind === g.kind);
            if (!items.length) return null;
            return (
              <fieldset key={g.kind} className="mon-vs-group" disabled={!d.can_select || busy}>
                <legend>{g.title} <small>— {g.hint}</small></legend>
                {items.map((s) => (
                  <label key={s.id} className={`mon-vs-item ${choice === s.id ? "checked" : ""}`}>
                    <input type="radio" name="video-source" value={s.id} checked={choice === s.id}
                           onChange={() => setChoice(s.id)} />
                    <span>{s.label}</span>
                    {d.active === s.id && <em className="mon-tone-ok">● в эфире</em>}
                    {d.selected === s.id && d.active !== s.id && <em className="mon-tone-warn">переключение…</em>}
                  </label>
                ))}
              </fieldset>
            );
          })}
          {!d.active && d.sources.length > 0 && (
            <p className="mon-muted">Видео идёт только во время выполнения задания.</p>
          )}
          {pending && <p className="mon-muted">Сервисный модуль переключит источник в течение нескольких секунд.</p>}
          {error && <p className="mon-tone-bad">{error}</p>}
          <div className="mon-dialog-foot">
            {d.can_select ? (
              <button className="mon-btn primary" disabled={busy || !choice || choice === d.selected} onClick={apply}>
                {busy ? "…" : "Применить"}
              </button>
            ) : <small className="mon-muted">Источник выбирает назначенный оператор.</small>}
            <small className="mon-muted">Выбор меняет только видео; на полёт он не влияет.</small>
          </div>
        </>
      )}
    </dialog>
  );
}
