import { useEffect, useRef, useState } from "react";
import type Hls from "hls.js";
import { api, errorText } from "../api";
import { Alert } from "./ui";

type State = "idle" | "starting" | "playing" | "waiting" | "error";
type Mode = "video" | "frames";

const FRAME_INTERVAL_MS = 1000;

/** Браузер воспроизводит H.264 (MediaSource для hls.js или встроенный HLS в Safari). */
function h264Supported(): boolean {
  const ms = typeof window !== "undefined" ? window.MediaSource : undefined;
  if (ms?.isTypeSupported?.('video/mp4; codecs="avc1.4d401f"')) return true;
  return !!document.createElement("video").canPlayType("application/vnd.apple.mpegurl");
}

/** Прямая видеотрансляция выполняемого заказа.
 *  «Видео» — HLS (hls.js; в Safari — встроенный плеер), задержка несколько секунд.
 *  «Кадры» — последний кадр раз в секунду: для браузеров без H.264 и медленных каналов.
 *  Открытие трансляции фиксируется сервером в журнале аудита. */
export function LiveVideo({ orderId }: { orderId: string }) {
  const canVideo = useRef(h264Supported()).current;
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [mode, setMode] = useState<Mode>(canVideo ? "video" : "frames");
  const [state, setState] = useState<State>("idle");
  const [frameUrl, setFrameUrl] = useState("");
  const [error, setError] = useState("");
  const base = `/api/v1/orders/${orderId}/live`;

  const teardown = () => {
    window.clearTimeout(timer.current);
    window.clearInterval(timer.current);
    hls.current?.destroy();
    hls.current = null;
    const v = video.current;
    if (v && v.getAttribute("src")) {
      v.removeAttribute("src");
      v.load();
    }
    setFrameUrl("");
  };

  const stop = () => {
    teardown();
    setState("idle");
  };

  useEffect(() => teardown, []);   // остановка при уходе со страницы или завершении заказа

  async function playVideo(playlist: string) {
    const v = video.current!;
    const { default: HlsLib } = await import("hls.js");
    if (HlsLib.isSupported()) {
      const h = new HlsLib({ enableWorker: false, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5,
                             manifestLoadingMaxRetry: 2, levelLoadingMaxRetry: 2 });
      hls.current = h;
      h.on(HlsLib.Events.MANIFEST_PARSED, () => v.play().catch(() => undefined));
      h.on(HlsLib.Events.FRAG_BUFFERED, () => setState("playing"));
      h.on(HlsLib.Events.ERROR, (_e, data) => {
        if (!data.fatal) return;
        // Поток ещё не поступает или прерван — повтор без перезагрузки страницы.
        setState("waiting");
        h.stopLoad();
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          h.loadSource(playlist);
          h.startLoad();
        }, 3000);
      });
      h.loadSource(playlist);
      h.attachMedia(v);
    } else {
      v.src = playlist;
      v.onplaying = () => setState("playing");
      v.onerror = () => setState("waiting");
      await v.play().catch(() => undefined);
    }
  }

  function playFrames() {
    const tick = () => setFrameUrl(`${base}/frame.jpg?t=${Date.now()}`);
    tick();
    timer.current = window.setInterval(tick, FRAME_INTERVAL_MS);
  }

  async function start(m: Mode = mode) {
    teardown();
    setError("");
    setState("starting");
    try {
      const { playlist_url } = await api.post<{ playlist_url: string }>(base, {});
      if (m === "video") await playVideo(playlist_url);
      else playFrames();
    } catch (err) {
      setState("error");
      setError(errorText(err));
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    if (state !== "idle" && state !== "error") start(m);
  }

  const active = state !== "idle" && state !== "error";
  return (
    <div className="live">
      <div className="row between">
        <div className="row">
          {state === "playing" && <span className="live-badge">● В ЭФИРЕ</span>}
          {(state === "waiting" || state === "starting") && <span className="live-badge waiting">Ожидание видеопотока…</span>}
          {!active && <span className="muted small">Видео с камеры дрона в реальном времени.</span>}
        </div>
        <div className="row">
          <select value={mode} onChange={(e) => switchMode(e.target.value as Mode)} aria-label="Режим трансляции">
            <option value="video" disabled={!canVideo}>Видео{canVideo ? "" : " (браузер не поддерживает H.264)"}</option>
            <option value="frames">Кадры (1 в секунду)</option>
          </select>
          {active ? <button className="btn" onClick={stop}>Остановить</button>
            : <button className="btn primary" onClick={() => start()}>Смотреть трансляцию</button>}
        </div>
      </div>
      <video ref={video} muted playsInline controls hidden={!active || mode !== "video"} aria-label="Трансляция с дрона" />
      {active && mode === "frames" && frameUrl && (
        <img className="live-frame" src={frameUrl} alt="Кадр трансляции с дрона"
             onLoad={() => setState("playing")} onError={() => setState("waiting")} />
      )}
      {!canVideo && <p className="muted small">Браузер не воспроизводит H.264 — трансляция показывается кадрами.</p>}
      <Alert tone="danger">{error}</Alert>
    </div>
  );
}
