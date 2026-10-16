import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api, errorText } from "../api";
import { useLive } from "../realtime";
import { LiveVideo } from "./LiveVideo";
import { Alert } from "./ui";

/** Статусы заказа, в которых заказчик может транслировать камеру (заказ в работе у оператора). */
export const CUSTOMER_STREAM_STATUSES = ["accepted", "awaiting_readiness", "ready", "in_progress", "awaiting_delivery_confirmation"];

const SEGMENT_MS = 2000;
const MAX_PENDING = 2;          // при медленном канале лишние сегменты пропускаются (важна актуальность)

function pickFormat(): { mime: string; type: string } | null {
  if (typeof MediaRecorder === "undefined") return null;
  for (const [mime, type] of [["video/webm;codecs=vp8", "video/webm"], ["video/webm;codecs=vp9", "video/webm"],
    ["video/webm", "video/webm"], ["video/mp4", "video/mp4"]]) {
    if (MediaRecorder.isTypeSupported(mime)) return { mime, type };
  }
  return null;
}

type Status = "idle" | "starting" | "live" | "error";

/** Трансляция с камеры заказчика (телефон или компьютер). Браузер записывает самостоятельные
 *  сегменты по 2 с и отправляет их на сервер; видео видят назначенные исполнители и администратор. */
export function CustomerCameraPublisher({ orderId }: { orderId: string }) {
  const preview = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const seq = useRef(0);
  const pending = useRef(0);
  const active = useRef(false);   // после остановки последний фрагмент не отправляется
  const [status, setStatus] = useState<Status>("idle");
  const [sent, setSent] = useState(0);
  const [skipped, setSkipped] = useState(0);
  const [error, setError] = useState("");
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [camera, setCamera] = useState("");
  const format = useRef(pickFormat()).current;
  const supported = !!format && !!navigator.mediaDevices?.getUserMedia;

  async function upload(blob: Blob) {
    if (!blob.size) return;
    if (pending.current >= MAX_PENDING) {
      setSkipped((n) => n + 1);
      return;
    }
    pending.current += 1;
    const n = seq.current++;
    try {
      await api.upload(`/api/v1/orders/${orderId}/customer-stream/segments`, blob, {
        "Content-Type": format!.type, "X-Segment-Seq": String(n), "X-Segment-Duration-Ms": String(SEGMENT_MS),
      });
      setSent((x) => x + 1);
    } catch (err) {
      if (err instanceof ApiError && [403, 404, 409].includes(err.status)) {
        stopLocal();
        setStatus("error");
      }
      setError(errorText(err));
    } finally {
      pending.current -= 1;
    }
  }

  // Каждый сегмент — отдельная запись со своим заголовком контейнера: сервер обрабатывает его независимо.
  function nextSegment() {
    const s = stream.current;
    if (!s || !format) return;
    const rec = new MediaRecorder(s, { mimeType: format.mime, videoBitsPerSecond: 900_000 });
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      if (active.current) upload(new Blob(chunks, { type: format.type }));
    };
    const prev = recorder.current;
    recorder.current = rec;
    rec.start();
    if (prev && prev.state !== "inactive") prev.stop();
  }

  function stopLocal() {
    active.current = false;
    window.clearInterval(timer.current);
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    recorder.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (preview.current) preview.current.srcObject = null;
  }

  async function start(deviceId = camera) {
    setError("");
    setStatus("starting");
    try {
      const quality = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 15 } };
      let s: MediaStream;
      try {
        s = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: deviceId ? { deviceId: { exact: deviceId }, ...quality } : { facingMode: { ideal: "environment" }, ...quality },
        });
      } catch (err) {
        // выбранная ранее камера отключена — пробуем любую доступную
        if (!deviceId || !(err instanceof DOMException) || !["NotFoundError", "OverconstrainedError"].includes(err.name)) throw err;
        s = await navigator.mediaDevices.getUserMedia({ audio: false, video: quality });
      }
      stream.current = s;
      if (preview.current) {
        preview.current.srcObject = s;
        preview.current.play().catch(() => undefined);
      }
      const devices = await navigator.mediaDevices.enumerateDevices();
      setCameras(devices.filter((d) => d.kind === "videoinput"));
      setCamera(s.getVideoTracks()[0]?.getSettings().deviceId ?? "");
      active.current = true;
      nextSegment();
      timer.current = window.setInterval(nextSegment, SEGMENT_MS);
      setStatus("live");
    } catch (err) {
      stopLocal();
      setStatus("error");
      setError(cameraErrorText(err));
    }
  }

  async function stop() {
    stopLocal();
    setStatus("idle");
    try {
      await api.del(`/api/v1/orders/${orderId}/customer-stream`);
    } catch {
      /* буфер в любом случае удалит фоновая задача */
    }
  }

  function switchCamera(id: string) {
    setCamera(id);
    if (status === "live") {
      stopLocal();
      start(id);
    }
  }

  useEffect(() => () => {
    if (stream.current) {
      stopLocal();
      api.del(`/api/v1/orders/${orderId}/customer-stream`).catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!supported) {
    return <p className="muted">Браузер не поддерживает трансляцию с камеры. Используйте актуальный Firefox, Chrome или Safari.</p>;
  }
  const live = status === "live";
  return (
    <div className="live cam-pub">
      <div className="row between">
        <div className="row">
          {live && <span className="live-badge">● ИДЁТ ТРАНСЛЯЦИЯ</span>}
          {!live && <span className="muted small">Покажите оператору место приёма груза или получателя.</span>}
        </div>
        <div className="row">
          {cameras.length > 1 && (
            <select value={camera} onChange={(e) => switchCamera(e.target.value)} aria-label="Камера">
              {cameras.map((c, i) => <option key={c.deviceId} value={c.deviceId}>{c.label || `Камера ${i + 1}`}</option>)}
            </select>
          )}
          {live ? <button className="btn" onClick={stop}>Остановить</button>
            : <button className="btn primary" disabled={status === "starting"} onClick={() => start()}>📹 Включить камеру</button>}
        </div>
      </div>
      <video ref={preview} muted playsInline autoPlay hidden={!live} aria-label="Ваша камера" />
      {live && <p className="muted small">Отправлено фрагментов: {sent}{skipped ? `, пропущено из-за медленной связи: ${skipped}` : ""}.</p>}
      <p className="muted small">Видео видят только назначенные оператор, координатор и администратор. Запись не сохраняется:
        на сервере держатся последние 30 секунд, они удаляются после остановки.</p>
      <Alert tone="danger">{error}</Alert>
    </div>
  );
}

/** Просмотр трансляции заказчика (оператор, координатор, администратор). */
export function CustomerCameraViewer({ orderId, variant = "card", main = false, children }: {
  orderId: string; variant?: "card" | "hud"; main?: boolean; children?: ReactNode;
}) {
  const st = useLive(() => api.get<{ active: boolean }>(`/api/v1/orders/${orderId}/customer-stream`), [orderId],
    (m) => m.kind === "customer_stream" && m.data.order_id === orderId);
  useEffect(() => {      // резервная проверка: трансляция могла прекратиться без сигнала
    const t = window.setInterval(() => st.reload(), 5000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (st.error) return <p className="muted">{errorText(st.error)}</p>;
  // main — в главном окне монитора полёта (вместо камеры дрона), с наложением-подписью источника
  if (main) {
    return st.data?.active
      ? <LiveVideo key={`${orderId}-customer-main`} orderId={orderId} source="customer" variant="hud" autoStart>{children}</LiveVideo>
      : <div className="live-hud">{children}<div className="live-hud-idle">Заказчик сейчас не транслирует камеру.</div></div>;
  }
  if (!st.data?.active) return <p className={variant === "hud" ? "mon-muted" : "muted"}>Заказчик сейчас не транслирует камеру.</p>;
  return <LiveVideo key={`${orderId}-customer`} orderId={orderId} source="customer" variant="card" autoStart={variant === "hud"} />;
}

/** Понятное сообщение вместо системного текста браузера (он бывает на английском). */
function cameraErrorText(err: unknown): string {
  if (err instanceof DOMException) {
    switch (err.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "Доступ к камере запрещён. Разрешите его в настройках браузера для этого сайта (значок слева от адреса).";
      case "NotFoundError":
      case "OverconstrainedError":
        return "Камера не найдена. Подключите камеру; в виртуальной машине пробросьте камеру хоста "
          + "(VirtualBox: «Устройства» → «Веб-камеры»). Затем обновите страницу.";
      case "NotReadableError":
      case "AbortError":
        return "Камера занята другой программой или недоступна системе. Закройте другие программы, использующие камеру, и повторите.";
    }
  }
  return errorText(err);
}
