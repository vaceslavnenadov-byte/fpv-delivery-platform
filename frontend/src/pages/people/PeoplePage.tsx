import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { Alert, Card, Empty, Pending, Tone } from "../../components/ui";
import { ROLE_LABELS, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { GroupSelect } from "./GroupSelect";
import { STATUS_TONE, loadUnits, type PersonCard, type PersonRow, type RoleRow } from "./common";

/** Пользователи (рекрутер и администратор): поиск и фильтры, новый пользователь с логином и паролем. */
export function PeoplePage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [unit, setUnit] = useState("");
  const [role, setRole] = useState("");
  const [adding, setAdding] = useState(false);
  const qs = new URLSearchParams(Object.entries({ q, status, org_unit: unit, role }).filter(([, v]) => v)).toString();
  const list = useLive(() => api.get<{ items: PersonRow[] }>(`/api/v1/people${qs ? `?${qs}` : ""}`), [qs]);
  const units = useLive(loadUnits, []);
  const roles = useLive(() => api.get<{ items: RoleRow[] }>("/api/v1/recruitment/roles"), []);
  return (
    <div className="stack">
      <div className="row between">
        <h1>Пользователи</h1>
        <button className="btn primary" onClick={() => setAdding((v) => !v)}>{adding ? "Скрыть форму" : "+ Новый пользователь"}</button>
      </div>
      {adding && <NewPerson units={units.data?.items ?? []} roles={roles.data?.items ?? []} />}
      <Card title="Список" actions={<div className="row wrap filters">
        <input placeholder="Поиск: ФИО, логин, почта, должность" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск" />
        <GroupSelect units={units.data?.items ?? []} value={unit} onChange={setUnit} empty="Все группы" label="Фильтр по группе" />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Фильтр по должности">
          <option value="">Все должности</option>
          {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Фильтр по статусу">
          <option value="">Все статусы</option>
          <option value="active">Активные</option><option value="suspended">Приостановленные</option>
          <option value="banned">Забаненные</option><option value="disabled">Отключённые</option>
        </select>
      </div>}>
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Никого не найдено</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Пользователь</th><th>Должность</th><th>Группа</th><th>Статус</th><th>Последний вход</th></tr></thead>
            <tbody>{list.data.items.map((u) => (
              <tr key={u.id}>
                <td><Link to={`/people/${u.id}`}><strong>{u.display_name}</strong></Link>
                  <div className="muted small">{u.username}{u.auth_source === "local" ? " · учётная запись приложения" : " · домен"}</div></td>
                <td className="small">{u.roles.map((r) => ROLE_LABELS[r.role] ?? r.role).join(", ") || "—"}
                  {u.position_title && <div className="muted">{u.position_title}</div>}</td>
                <td className="small">{u.org_unit?.path ?? "—"}</td>
                <td><Tone tone={STATUS_TONE[u.status] ?? "muted"}>{u.status_label}</Tone></td>
                <td className="small">{fmtDate(u.last_login_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}

function NewPerson({ units, roles }: { units: { id: string; parent_id: string | null; name: string; path: string; members: number }[]; roles: RoleRow[] }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [f, setF] = useState({ username: "", password: "", last_name: "", first_name: "", middle_name: "", position_title: "",
                               email: "", phone: "", profile_notes: "", org_unit_id: "" });
  const [picked, setPicked] = useState<string[]>(["customer"]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = Object.fromEntries(Object.entries({ ...f, roles: picked }).filter(([, v]) => v !== ""));
      const card = await api.post<PersonCard>("/api/v1/people", body);
      navigate(`/people/${card.id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Новый пользователь (учётная запись приложения)">
      <form className="stack" onSubmit={submit}>
        <div className="form grid4">
          <label>Фамилия<input value={f.last_name} onChange={set("last_name")} required maxLength={80} /></label>
          <label>Имя<input value={f.first_name} onChange={set("first_name")} required maxLength={80} /></label>
          <label>Отчество<input value={f.middle_name} onChange={set("middle_name")} maxLength={80} /></label>
          <label>Должность в организации<input value={f.position_title} onChange={set("position_title")} maxLength={160} placeholder="например, диспетчер" /></label>
          <label>Логин<input value={f.username} onChange={set("username")} required minLength={3} maxLength={64} autoComplete="off"
                             pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}" title="латинские буквы, цифры, точка, дефис, подчёркивание" /></label>
          <label>Пароль<input type="password" value={f.password} onChange={set("password")} required minLength={10} autoComplete="new-password"
                              title="не короче 10 символов, буквы и цифры" /></label>
          <label>Почта<input type="email" value={f.email} onChange={set("email")} maxLength={200} /></label>
          <label>Телефон<input value={f.phone} onChange={set("phone")} placeholder="+7 900 000-00-00" /></label>
          <label className="span2">Группа / подгруппа<GroupSelect units={units} value={f.org_unit_id} onChange={(v) => setF({ ...f, org_unit_id: v })} /></label>
          <label className="span2">Примечание<input value={f.profile_notes} onChange={set("profile_notes")} maxLength={2000} /></label>
        </div>
        <fieldset className="role-picks">
          <legend>Должность в системе (роль)</legend>
          {roles.map((r) => (
            <label key={r.code} className="check" title={r.description}>
              <input type="checkbox" checked={picked.includes(r.code)}
                     onChange={(e) => setPicked(e.target.checked ? [...picked, r.code] : picked.filter((x) => x !== r.code))} />
              {r.name}
            </label>
          ))}
        </fieldset>
        {!can("user.manage") && <p className="muted small">Роль администратора выдаёт только администратор.</p>}
        {error && <Alert>{error}</Alert>}
        <div><button className="btn primary" disabled={busy}>Сохранить</button></div>
      </form>
    </Card>
  );
}
