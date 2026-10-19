import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api";
import { Modal } from "./ui";

// Готовность к работе оператора, координатора и техника: кнопки в шапке клиента.
// Смена подтверждается во всплывающем окне; страницы роли узнают о ней через событие окна.

export type WorkRole = "operator" | "coordinator" | "technician";
interface Readiness { role: WorkRole; ready: boolean; label: string; moved?: number; note?: string }

const EVENT = "fpv:readiness";

const EFFECT: Record<WorkRole, { on: string; off: string }> = {
  operator: {
    on: "Вам начнут предлагаться заказы: сразу — ждущие заказы, на которые у вас есть квалификация, и последние поступившие, пока нагрузка операторов не выровняется.",
    off: "Предложенные вам, но не принятые заказы перейдут к другим операторам с нужной квалификацией; если таких нет — заказы подождут первого подходящего. Принятое задание остаётся за вами.",
  },
  coordinator: {
    on: "Вам начнут назначаться новые заказы, и на вас сразу перейдут последние поступившие заказы — нагрузка координаторов выровняется.",
    off: "Ваши заказы, которые ещё не вылетели, сразу перейдут к другим координаторам; заказы в полёте останутся за вами. Новые заказы вам назначаться не будут.",
  },
  technician: {
    on: "Заказы на дронах вашего парка, которые вели техники зоны, вернутся к вам.",
    off: "Заказы на дронах вашего парка перейдут готовым техникам вашей зоны обслуживания — им откроется доступ к этим дронам для подготовки к полёту.",
  },
};

export function useReadiness(role: WorkRole | null): Readiness | null {
  const [state, setState] = useState<Readiness | null>(null);
  const load = useCallback(() => {
    if (!role) return;
    api.get<Readiness>(`/api/v1/me/readiness?role=${role}`).then(setState, () => setState(null));
  }, [role]);
  useEffect(() => {
    load();
    const on = (e: Event) => { if ((e as CustomEvent<Readiness>).detail?.role === role) setState((e as CustomEvent<Readiness>).detail); };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, [load, role]);
  return state;
}

export function ReadinessSwitch({ role }: { role: WorkRole }) {
  const state = useReadiness(role);
  const [ask, setAsk] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  if (!state) return null;
  const confirm = async () => {
    if (ask === null) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.put<Readiness>("/api/v1/me/readiness", { role, ready: ask });
      window.dispatchEvent(new CustomEvent(EVENT, { detail: r }));
      setNote(r.note ?? "");
      if (r.note) window.setTimeout(() => setNote(""), 8000);
      setAsk(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {note && <span className="readiness-note small" role="status">{note}</span>}
      <div className="readiness" role="group" aria-label="Готовность к работе">
        <button type="button" className={`btn readiness-on ${state.ready ? "active" : ""}`} aria-pressed={state.ready}
                onClick={() => !state.ready && setAsk(true)}>● Готов к работе</button>
        <button type="button" className={`btn readiness-off ${!state.ready ? "active" : ""}`} aria-pressed={!state.ready}
                onClick={() => state.ready && setAsk(false)}>● Не готов к работе</button>
      </div>
      {ask !== null && (
        <Modal title={ask ? "Перейти в статус «Готов к работе»?" : "Перейти в статус «Не готов к работе»?"}
               onClose={() => !busy && setAsk(null)} footer={<>
          <button type="button" className="btn" onClick={() => setAsk(null)} disabled={busy}>Отмена</button>
          <button type="button" className={`btn ${ask ? "ok-fill" : "danger-fill"}`} onClick={() => void confirm()} disabled={busy}>
            {busy ? "Сохранение…" : ask ? "Подтвердить: готов к работе" : "Подтвердить: не готов к работе"}
          </button>
        </>}>
          <p>{ask ? EFFECT[role].on : EFFECT[role].off}</p>
          {error && <p className="error">{error}</p>}
        </Modal>
      )}
    </>
  );
}
