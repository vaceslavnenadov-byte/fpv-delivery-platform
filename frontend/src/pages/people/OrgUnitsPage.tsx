import { useState } from "react";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { Alert, Card, Empty, Pending } from "../../components/ui";
import { useLive } from "../../realtime";
import { loadUnits, treeOrder, type OrgUnit } from "./common";

/** Внутренние группы и подгруппы (компании, отделы, кабинеты …): информационные, на права не влияют. */
export function OrgUnitsPage() {
  const { can } = useAuth();
  const edit = can("org.manage");
  const units = useLive(loadUnits, []);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [rename, setRename] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState<string | null>(null);      // id родителя или "root"
  const [name, setName] = useState("");
  async function run(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "ok", text: ok });
      units.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }
  const add = (parent: string | null) => run(() => api.post("/api/v1/org-units", { name, parent_id: parent }), `Группа «${name}» создана`)
    .then(() => { setAdding(null); setName(""); });
  if (!units.data) return <Pending />;
  const rows = treeOrder(units.data.items);
  const addForm = (parent: string | null) => (
    <form className="inline-form" onSubmit={(e) => { e.preventDefault(); void add(parent); }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название" autoFocus required maxLength={160}
             aria-label="Название новой группы" />
      <button className="btn small primary">Создать</button>
      <button type="button" className="btn small ghost" onClick={() => setAdding(null)}>Отмена</button>
    </form>
  );
  return (
    <div className="stack">
      <div className="row between">
        <h1>Группы пользователей</h1>
        {edit && <button className="btn primary" onClick={() => { setAdding("root"); setName(""); }}>+ Группа верхнего уровня</button>}
      </div>
      <p className="muted">Компании, отделы, кабинеты и любые другие группы — только для сортировки и поиска пользователей, на права
        доступа они не влияют. В каждой группе можно создать подгруппу, глубина не ограничена.
        {!edit && " Изменять дерево групп может администратор."}</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card>
        {adding === "root" && addForm(null)}
        {rows.length === 0 ? <Empty>Групп пока нет</Empty> : (
          <ul className="org-tree">
            {rows.map((u: OrgUnit & { depth: number }) => (
              <li key={u.id} style={{ paddingLeft: `${u.depth * 24}px` }}>
                <div className="row wrap org-row">
                  <span className="org-branch">{u.depth ? "└" : "■"}</span>
                  {rename[u.id] !== undefined ? (
                    <form className="inline-form" onSubmit={(e) => { e.preventDefault();
                      void run(() => api.patch(`/api/v1/org-units/${u.id}`, { name: rename[u.id] }), "Группа переименована")
                        .then(() => setRename(({ [u.id]: _, ...r }) => r)); }}>
                      <input value={rename[u.id]} onChange={(e) => setRename({ ...rename, [u.id]: e.target.value })} autoFocus
                             aria-label={`Новое название группы ${u.name}`} maxLength={160} />
                      <button className="btn small primary">Сохранить</button>
                      <button type="button" className="btn small ghost" onClick={() => setRename(({ [u.id]: _, ...r }) => r)}>Отмена</button>
                    </form>
                  ) : <strong>{u.name}</strong>}
                  <span className="muted small">пользователей: {u.members}</span>
                  {edit && rename[u.id] === undefined && (
                    <>
                      <button className="btn small" onClick={() => { setAdding(u.id); setName(""); }}>+ Подгруппа</button>
                      <button className="btn small ghost" onClick={() => setRename({ ...rename, [u.id]: u.name })}>Переименовать</button>
                      <button className="btn small ghost danger" onClick={() => confirm(`Удалить группу «${u.name}»?`) &&
                        void run(() => api.del(`/api/v1/org-units/${u.id}`), "Группа удалена")}>Удалить</button>
                    </>
                  )}
                </div>
                {adding === u.id && <div style={{ paddingLeft: 24 }}>{addForm(u.id)}</div>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
