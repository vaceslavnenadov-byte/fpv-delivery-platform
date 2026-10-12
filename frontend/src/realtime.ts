// Канал обновлений в реальном времени. Сообщения — только сигналы; данные перечитываются через REST.
// При обрыве соединения выполняется переподключение с экспоненциальной задержкой,
// после восстановления подписчики получают событие "resync" и синхронизируют состояние.
import { useEffect, useRef, useState } from "react";

export interface RealtimeMessage {
  kind: string;
  data: Record<string, unknown>;
}

type Listener = (msg: RealtimeMessage) => void;

class RealtimeChannel {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<(s: boolean) => void>();
  private retry = 0;
  private timer: number | null = null;
  private stopped = true;
  connected = false;

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  private setConnected(v: boolean) {
    this.connected = v;
    this.statusListeners.forEach((l) => l(v));
  }

  private connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`);
    this.ws = ws;
    ws.onopen = () => {
      const wasReconnect = this.retry > 0;
      this.retry = 0;
      this.setConnected(true);
      if (wasReconnect) this.emit({ kind: "resync", data: {} });
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as RealtimeMessage;
        if (msg.kind !== "ping") this.emit(msg);
      } catch {
        /* игнорируем некорректные сообщения */
      }
    };
    ws.onclose = (ev) => {
      this.setConnected(false);
      if (this.stopped || ev.code === 4401) return;
      this.retry += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(this.retry, 5));
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }

  private emit(msg: RealtimeMessage) {
    this.listeners.forEach((l) => l(msg));
  }

  subscribe(l: Listener) {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  onStatus(l: (s: boolean) => void) {
    this.statusListeners.add(l);
    return () => this.statusListeners.delete(l);
  }
}

export const realtime = new RealtimeChannel();

/** Вызывает onEvent при каждом подходящем событии и при восстановлении связи. */
export function useRealtime(onEvent: (msg: RealtimeMessage) => void, filter?: (msg: RealtimeMessage) => boolean) {
  const cb = useRef(onEvent);
  cb.current = onEvent;
  const flt = useRef(filter);
  flt.current = filter;
  useEffect(() => {
    const unsub = realtime.subscribe((msg) => {
      if (msg.kind === "resync" || !flt.current || flt.current(msg)) cb.current(msg);
    });
    return () => {
      unsub();
    };
  }, []);
}

export function useRealtimeStatus(): boolean {
  const [s, setS] = useState(realtime.connected);
  useEffect(() => {
    const unsub = realtime.onStatus(setS);
    return () => {
      unsub();
    };
  }, []);
  return s;
}

/** Загрузка данных с автоматическим обновлением по событиям реального времени. */
export function useLive<T>(loader: () => Promise<T>, deps: unknown[], filter?: (m: RealtimeMessage) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);
  const load = useRef(loader);
  load.current = loader;
  useEffect(() => {
    let alive = true;
    load.current().then(
      (d) => alive && (setData(d), setError(null)),
      (e) => alive && setError(e),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);
  useRealtime(() => setVersion((v) => v + 1), filter);
  return { data, error, reload: () => setVersion((v) => v + 1), setData };
}
