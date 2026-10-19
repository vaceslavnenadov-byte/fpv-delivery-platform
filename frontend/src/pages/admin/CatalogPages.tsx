import { Fragment, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Alert, Card, Empty, Modal, Pending, useSort } from "../../components/ui";
import { fmtDate } from "../../labels";
import { loadCargoTypes, loadConsignees, loadDroneModels, type CargoType, type Consignee, type DroneModel } from "../../catalogs";
import { PRIORITY_LABELS } from "../../components/RepeatBadges";
import { useLive } from "../../realtime";

// Справочники администратора. Строка списка открывает карточку во всплывающем окне:
// «Отмена», «Сохранить» (зелёная), «Удалить» (красная).

function CardButtons({ onCancel, onDelete, busy }: { onCancel: () => void; onDelete?: () => void; busy: boolean }) {
  return (
    <>
      {onDelete && <button type="button" className="btn danger-fill" disabled={busy} onClick={onDelete}>Удалить</button>}
      <span className="spacer" />
      <button type="button" className="btn" onClick={onCancel}>Отмена</button>
      <button type="submit" form="catalog-card" className="btn ok-fill" disabled={busy}>Сохранить</button>
    </>
  );
}

function useCardActions(onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

// --- Типы грузов ---

export function CargoTypesPage() {
  const list = useLive(loadCargoTypes, []);
  const [open, setOpen] = useState<CargoType | "new" | null>(null);
  const { sorted, th } = useSort(list.data ?? [], "name");
  return (
    <div className="stack">
      <div className="row between">
        <h1>Типы грузов</h1>
        <button className="btn primary" onClick={() => setOpen("new")}>+ Добавить тип груза</button>
      </div>
      <p className="muted">Из этого списка заказчик выбирает «Тип груза»; масса каждого груза в заказе не может превышать
        максимальную для его типа. Удалённый тип, уже встречавшийся в заказах, скрывается из выбора, а в истории заказов остаётся.</p>
      <Card>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : sorted.length === 0 ? <Empty>Список пуст</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{th("name", "Название")}{th("category", "Тип")}{th("max_weight_g", "Макс. масса, г", true)}{th("updated_at", "Изменён")}</tr></thead>
              <tbody>{sorted.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => setOpen(c)}>
                  <td><button className="link" onClick={() => setOpen(c)}>{c.name}</button></td>
                  <td>{c.category || "—"}</td><td className="num">{c.max_weight_g}</td>
                  <td className="small">{fmtDate(c.updated_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <CargoCard item={open === "new" ? null : open} onClose={() => setOpen(null)}
                          onDone={() => { setOpen(null); list.reload(); }} />}
    </div>
  );
}

function CargoCard({ item, onClose, onDone }: { item: CargoType | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: item?.name ?? "", category: item?.category ?? "", max_weight_g: item?.max_weight_g ?? 1000 });
  const { busy, error, run } = useCardActions(onDone);
  const save = (e: FormEvent) => {
    e.preventDefault();
    run(() => item ? api.patch(`/api/v1/catalog/cargo-types/${item.id}`, f) : api.post("/api/v1/catalog/cargo-types", f));
  };
  const remove = () => {
    if (item && confirm(`Удалить тип груза «${item.name}»?`)) run(() => api.del(`/api/v1/catalog/cargo-types/${item.id}`));
  };
  return (
    <Modal title={item ? `Тип груза: ${item.name}` : "Новый тип груза"} onClose={onClose}
           footer={<CardButtons onCancel={onClose} onDelete={item ? remove : undefined} busy={busy} />}>
      <form id="catalog-card" className="form grid2" onSubmit={save}>
        <label className="span2">Название<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={120} autoFocus /></label>
        <label>Тип<input value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} maxLength={120}
                         placeholder="например, Медицина" /></label>
        <label>Максимальная масса, г<input type="number" min={1} max={100000} value={f.max_weight_g}
                         onChange={(e) => setF({ ...f, max_weight_g: Number(e.target.value) })} required /></label>
      </form>
      <Alert>{error}</Alert>
    </Modal>
  );
}

// --- Типы БПЛА ---

export const DRONE_MODEL_LABELS: [keyof DroneModel & string, string][] = [
  ["model", "Модель"], ["manufacturer", "Производитель"], ["link_type", "Тип связи"], ["construction", "Конструкция"],
  ["engine_type", "Тип двигателя"], ["max_range_m", "Дальность, м"], ["max_payload_g", "Грузоподъёмность, г"],
];

export function DroneModelsPage() {
  const list = useLive(loadDroneModels, []);
  const [open, setOpen] = useState<DroneModel | "new" | null>(null);
  const { sorted, th } = useSort(list.data ?? [], "model");
  return (
    <div className="stack">
      <div className="row between">
        <h1>Типы БПЛА</h1>
        <button className="btn primary" onClick={() => setOpen("new")}>+ Добавить тип БПЛА</button>
      </div>
      <p className="muted">Конфигурации дронов. Техник выбирает конфигурацию при заведении дрона в парк — параметры подставляются
        сами, серийный и учётный номера техник вводит вручную. Изменение типа не меняет параметры уже заведённых дронов.</p>
      <Card>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : sorted.length === 0 ? <Empty>Список пуст</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{DRONE_MODEL_LABELS.map(([k, l]) => <Fragment key={k}>{th(k, l, k.startsWith("max_"))}</Fragment>)}</tr></thead>
              <tbody>{sorted.map((m) => (
                <tr key={m.id} className="clickable" onClick={() => setOpen(m)}>
                  <td><button className="link" onClick={() => setOpen(m)}>{m.model}</button></td>
                  <td>{m.manufacturer}</td><td>{m.link_type || "—"}</td><td>{m.construction || "—"}</td>
                  <td>{m.engine_type || "—"}</td><td className="num">{m.max_range_m}</td><td className="num">{m.max_payload_g}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <DroneModelCard item={open === "new" ? null : open} onClose={() => setOpen(null)}
                               onDone={() => { setOpen(null); list.reload(); }} />}
    </div>
  );
}

function DroneModelCard({ item, onClose, onDone }: { item: DroneModel | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({
    model: item?.model ?? "", manufacturer: item?.manufacturer ?? "", link_type: item?.link_type ?? "",
    construction: item?.construction ?? "", engine_type: item?.engine_type ?? "",
    max_range_m: item?.max_range_m ?? 10000, max_payload_g: item?.max_payload_g ?? 1500,
  });
  const { busy, error, run } = useCardActions(onDone);
  const save = (e: FormEvent) => {
    e.preventDefault();
    run(() => item ? api.patch(`/api/v1/catalog/drone-models/${item.id}`, f) : api.post("/api/v1/catalog/drone-models", f));
  };
  const remove = () => {
    if (item && confirm(`Удалить тип БПЛА «${item.manufacturer} ${item.model}»?`)) run(() => api.del(`/api/v1/catalog/drone-models/${item.id}`));
  };
  const text = (k: "model" | "manufacturer" | "link_type" | "construction" | "engine_type") =>
    (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={item ? `Тип БПЛА: ${item.manufacturer} ${item.model}` : "Новый тип БПЛА"} onClose={onClose}
           footer={<CardButtons onCancel={onClose} onDelete={item ? remove : undefined} busy={busy} />}>
      <form id="catalog-card" className="form grid2" onSubmit={save}>
        <label>Модель<input value={f.model} onChange={text("model")} required maxLength={100} autoFocus /></label>
        <label>Производитель<input value={f.manufacturer} onChange={text("manufacturer")} required minLength={2} maxLength={100} /></label>
        <label>Тип связи<input value={f.link_type} onChange={text("link_type")} maxLength={100} placeholder="например, 4G/LTE, радио 2,4 ГГц" /></label>
        <label>Конструкция<input value={f.construction} onChange={text("construction")} maxLength={100} placeholder="например, квадрокоптер" /></label>
        <label>Тип двигателя<input value={f.engine_type} onChange={text("engine_type")} maxLength={100} placeholder="например, бесколлекторный электрический" /></label>
        <span />
        <label>Дальность, м<input type="number" min={500} max={100000} value={f.max_range_m}
                                  onChange={(e) => setF({ ...f, max_range_m: Number(e.target.value) })} required /></label>
        <label>Грузоподъёмность, г<input type="number" min={50} max={25000} value={f.max_payload_g}
                                  onChange={(e) => setF({ ...f, max_payload_g: Number(e.target.value) })} required /></label>
      </form>
      <Alert>{error}</Alert>
    </Modal>
  );
}

// --- Типы грузополучателей ---

const PRIORITY_RANK: Record<string, number> = { highest: 0, high: 1, normal: 2, low: 3 };
const PRIORITY_TONE: Record<string, string> = { highest: "flag-red", high: "flag-orange", normal: "", low: "flag-muted" };

export function ConsigneesPage() {
  const list = useLive(loadConsignees, []);
  const [open, setOpen] = useState<Consignee | "new" | null>(null);
  const rows = (list.data ?? []).map((c) => ({ ...c, priority_rank: PRIORITY_RANK[c.priority] ?? 9, cargo: c.recommended_cargo_name ?? "" }));
  const { sorted, th } = useSort(rows, "name");
  return (
    <div className="stack">
      <div className="row between">
        <h1>Типы грузополучателей</h1>
        <button className="btn primary" onClick={() => setOpen("new")}>+ Добавить грузополучателя</button>
      </div>
      <p className="muted">Из этого списка заказчик выбирает грузополучателя на первом шаге заказа. Приоритет определяет порядок
        заказов во всех списках: «Наивысший» — всегда первым и в красной рамке, затем повторные и «Высокий», «Обычный», «Низкий».</p>
      <Card>
        {list.error ? <Alert>{errorText(list.error)}</Alert> : !list.data ? <Pending /> : sorted.length === 0 ? <Empty>Список пуст</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{th("name", "Название")}{th("kind", "Тип")}{th("cargo", "Рекомендованный груз")}{th("location", "Местоположение")}
                {th("priority_rank", "Приоритет")}{th("comment", "Комментарий")}</tr></thead>
              <tbody>{sorted.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => setOpen(c)}>
                  <td><button className="link" onClick={() => setOpen(c)}>{c.name}</button></td>
                  <td>{c.kind || "—"}</td><td>{c.recommended_cargo_name ?? "—"}</td><td className="small">{c.location || "—"}</td>
                  <td>{PRIORITY_TONE[c.priority] ? <span className={PRIORITY_TONE[c.priority]}>{c.priority_label}</span> : c.priority_label}</td>
                  <td className="small">{c.comment || "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <ConsigneeCard item={open === "new" ? null : open} onClose={() => setOpen(null)}
                              onDone={() => { setOpen(null); list.reload(); }} />}
    </div>
  );
}

function ConsigneeCard({ item, onClose, onDone }: { item: Consignee | null; onClose: () => void; onDone: () => void }) {
  const cargo = useLive(loadCargoTypes, []);
  const [f, setF] = useState({
    name: item?.name ?? "", kind: item?.kind ?? "", recommended_cargo: item?.recommended_cargo ?? "",
    location: item?.location ?? "", priority: item?.priority ?? "normal", comment: item?.comment ?? "",
  });
  const { busy, error, run } = useCardActions(onDone);
  const body = () => ({ ...f, recommended_cargo: f.recommended_cargo || null });
  const save = (e: FormEvent) => {
    e.preventDefault();
    run(() => item ? api.patch(`/api/v1/catalog/consignees/${item.id}`, body()) : api.post("/api/v1/catalog/consignees", body()));
  };
  const remove = () => {
    if (item && confirm(`Удалить грузополучателя «${item.name}»?`)) run(() => api.del(`/api/v1/catalog/consignees/${item.id}`));
  };
  const text = (k: "name" | "kind" | "location" | "comment") => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={item ? `Грузополучатель: ${item.name}` : "Новый грузополучатель"} onClose={onClose}
           footer={<CardButtons onCancel={onClose} onDelete={item ? remove : undefined} busy={busy} />}>
      <form id="catalog-card" className="form grid2" onSubmit={save}>
        <label className="span2">Название<input value={f.name} onChange={text("name")} required maxLength={200} autoFocus /></label>
        <label>Тип грузополучателя<input value={f.kind} onChange={text("kind")} maxLength={120} placeholder="например, больница" /></label>
        <label>Рекомендованный груз
          <select value={f.recommended_cargo} onChange={(e) => setF({ ...f, recommended_cargo: e.target.value })}>
            <option value="">— не задан —</option>
            {(cargo.data ?? []).map((c: CargoType) => <option key={c.code} value={c.code}>{c.name}</option>)}
          </select>
        </label>
        <label className="span2">Местоположение (комментарий)<input value={f.location} onChange={text("location")} maxLength={500}
          placeholder="адрес, корпус, как найти" /></label>
        <label>Приоритет
          <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value as Consignee["priority"] })}>
            {(["low", "normal", "high", "highest"] as const).map((k) => <option key={k} value={k}>{PRIORITY_LABELS[k]}</option>)}
          </select>
        </label>
        <span />
        <label className="span2">Комментарий (необязательно)<textarea value={f.comment} onChange={text("comment")} maxLength={2000} /></label>
      </form>
      <Alert>{error}</Alert>
    </Modal>
  );
}
