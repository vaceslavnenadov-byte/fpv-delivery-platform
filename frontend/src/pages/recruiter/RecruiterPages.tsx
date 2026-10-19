import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { Alert, Card, Empty, Pending, Tone } from "../../components/ui";
import { ROLE_LABELS, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { PROFILE_LABEL, PROFILE_TONE } from "../Profile";

// Клиент рекрутера: добавление пользователей из доменного каталога, выдача ролей,
// проверка профилей и заявок на допуск операторов. Сервер сам ограничивает набор ролей:
// администратора и рекрутера назначает только администратор.

type Msg = { tone: string; text: string } | null;

interface RecruitUser {
  id: string; username: string; display_name: string; email: string | null; phone: string | null;
  status: string; directory_enabled: boolean; roles: { role: string; source: string }[];
  profile_completed: boolean; profile_status: "pending" | "verified" | "rejected";
  profile_review_notes: string | null; profile_reviewed_at: string | null; profile_reviewed_by: string | null;
  registered_by: string | null;
  profile_verified?: boolean;
  operator: { status: string; license_number: string; notes: string } | null;
  last_login_at: string | null; created_at: string;
}
interface RoleRow { code: string; name: string; description: string }
interface OperatorRow {
  user_id: string; display_name: string; username: string; status: string; license_number: string;
  notes: string | null; created_at: string; decided_at: string | null;
}

const OP_LABEL: Record<string, string> = { pending: "ждёт решения", approved: "допущен", rejected: "отклонён", suspended: "приостановлен" };

async function run(setMsg: (m: Msg) => void, fn: () => Promise<unknown>, ok: string, after?: () => void) {
  try {
    await fn();
    setMsg({ tone: "ok", text: ok });
    after?.();
  } catch (err) {
    setMsg({ tone: "danger", text: errorText(err) });
  }
}

function useRoles() {
  return useLive(() => api.get<{ items: RoleRow[] }>("/api/v1/recruitment/roles"), []);
}

export function RecruiterUsersPage() {
  const [q, setQ] = useState("");
  const users = useLive(() => api.get<{ items: RecruitUser[] }>(
    `/api/v1/recruitment/users${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q], (m) => m.kind.startsWith("notification"));
  const roles = useRoles();
  const [msg, setMsg] = useState<Msg>(null);
  const [username, setUsername] = useState("");
  const [newRoles, setNewRoles] = useState<string[]>(["customer"]);

  const register = (e: FormEvent) => {
    e.preventDefault();
    void run(setMsg, () => api.post("/api/v1/recruitment/users", { username: username.trim(), roles: newRoles }),
      `Пользователь ${username.trim()} добавлен в систему`, () => { setUsername(""); users.reload(); });
  };
  const grant = (u: RecruitUser, role: string, grantIt: boolean) =>
    run(setMsg, () => api.post(`/api/v1/recruitment/users/${u.id}/roles`, { role, grant: grantIt }),
      grantIt ? `Роль «${ROLE_LABELS[role] ?? role}» выдана` : `Роль «${ROLE_LABELS[role] ?? role}» снята`, users.reload);
  const assignable = roles.data?.items ?? [];

  return (
    <div className="stack">
      <h1>Пользователи и роли</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title="Добавить пользователя">
        <form className="form recruit-form" onSubmit={register}>
          <label>Доменная учётная запись
            <input value={username} onChange={(e) => setUsername(e.target.value)} required minLength={2}
                   placeholder="i.ivanov" autoComplete="off" />
          </label>
          <fieldset className="role-picks">
            <legend>Роли</legend>
            {assignable.map((r) => (
              <label key={r.code} className="check" title={r.description}>
                <input type="checkbox" checked={newRoles.includes(r.code)}
                       onChange={(e) => setNewRoles(e.target.checked ? [...newRoles, r.code] : newRoles.filter((x) => x !== r.code))} />
                {r.name}
              </label>
            ))}
          </fieldset>
          <div><button className="btn primary" disabled={!username.trim()}>Добавить в систему</button></div>
        </form>
        <p className="muted small">Учётная запись должна уже существовать в доменном каталоге — её создаёт администратор домена.
          После добавления пользователь входит под своим доменным логином и паролем.</p>
      </Card>
      <Card title="Пользователи" actions={<input placeholder="Поиск по имени или логину" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск пользователей" />}>
        {!users.data ? <Pending /> : users.data.items.length === 0 ? <Empty>Никого не найдено</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Пользователь</th><th>Роли</th><th>Профиль</th><th>Допуск оператора</th><th>Последний вход</th></tr></thead>
            <tbody>{users.data.items.map((u) => {
              const has = new Set(u.roles.map((r) => r.role));
              return (
                <tr key={u.id}>
                  <td><strong>{u.display_name}</strong><div className="muted small">{u.username}</div>
                    {!u.directory_enabled && <Tone tone="danger">заблокирован в домене</Tone>}
                    {u.registered_by && <div className="muted small">добавил(а): {u.registered_by}</div>}</td>
                  <td>
                    <div className="role-chips">
                      {u.roles.map((r) => (
                        <span key={`${r.role}-${r.source}`} className={`chip ${r.source}`}
                              title={r.source === "domain" ? "Из доменной группы — меняется в каталоге" : "Выдана вручную"}>
                          {ROLE_LABELS[r.role] ?? r.role}
                          {r.source === "manual" && assignable.some((a) => a.code === r.role) && (
                            <button type="button" aria-label={`Снять роль ${ROLE_LABELS[r.role] ?? r.role}`}
                                    onClick={() => confirm(`Снять роль «${ROLE_LABELS[r.role] ?? r.role}» у ${u.display_name}?`) && void grant(u, r.role, false)}>×</button>
                          )}
                        </span>
                      ))}
                      {u.roles.length === 0 && <span className="muted small">нет ролей — вход невозможен</span>}
                    </div>
                    <select value="" aria-label={`Выдать роль ${u.display_name}`}
                            onChange={(e) => { if (e.target.value) void grant(u, e.target.value, true); }}>
                      <option value="">+ выдать роль…</option>
                      {assignable.filter((r) => !has.has(r.code)).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
                    </select>
                  </td>
                  <td>{u.profile_completed
                    ? <ProfileReview u={u} setMsg={setMsg} onDone={users.reload} compact />
                    : <span className="muted small">не заполнен</span>}</td>
                  <td className="small">{u.operator ? <><Tone tone={u.operator.status === "approved" ? "ok" : u.operator.status === "pending" ? "warn" : "danger"}>
                    {OP_LABEL[u.operator.status] ?? u.operator.status}</Tone><div className="muted">№ {u.operator.license_number}</div></> : "—"}</td>
                  <td className="small">{fmtDate(u.last_login_at)}</td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}

function ProfileReview({ u, setMsg, onDone, compact = false }: {
  u: RecruitUser; setMsg: (m: Msg) => void; onDone: () => void; compact?: boolean;
}) {
  const { me } = useAuth();
  const [rejecting, setRejecting] = useState(false);
  const [notes, setNotes] = useState("");
  const review = (approve: boolean) => run(setMsg, () => api.post(`/api/v1/recruitment/users/${u.id}/profile`, { approve, notes }),
    approve ? `Профиль ${u.display_name} подтверждён` : `Профиль ${u.display_name} возвращён на доработку`,
    () => { setRejecting(false); setNotes(""); onDone(); });
  const self = u.id === me?.id;
  return (
    <div className="profile-review">
      <Tone tone={PROFILE_TONE[u.profile_status]}>{PROFILE_LABEL[u.profile_status]}</Tone>
      {u.profile_status !== "pending" && u.profile_reviewed_by && (
        <div className="muted small">{u.profile_reviewed_by}, {fmtDate(u.profile_reviewed_at)}{u.profile_review_notes && ` — ${u.profile_review_notes}`}</div>
      )}
      {!compact && (
        <div className="small">{u.phone ?? "телефон не указан"} · {u.email ?? "почта не указана"}</div>
      )}
      {!self && u.profile_status !== "verified" && !rejecting && (
        <div className="row wrap">
          <button className="btn small primary" onClick={() => void review(true)}>✓ Подтвердить</button>
          {u.profile_status === "pending" && <button className="btn small" onClick={() => setRejecting(true)}>Вернуть на доработку</button>}
        </div>
      )}
      {rejecting && (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); void review(false); }}>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} required minLength={5} autoFocus
                 placeholder="Что исправить — увидит пользователь" aria-label="Что исправить в профиле" />
          <button className="btn small">Вернуть</button>
          <button type="button" className="btn small ghost" onClick={() => setRejecting(false)}>Отмена</button>
        </form>
      )}
    </div>
  );
}

export function RecruiterProfilesPage() {
  const list = useLive(() => api.get<{ items: RecruitUser[] }>("/api/v1/recruitment/users?profile_status=pending"), [],
    (m) => m.kind.startsWith("notification"));
  const [msg, setMsg] = useState<Msg>(null);
  return (
    <div className="stack">
      <h1>Проверка профилей</h1>
      <p className="muted">Анкеты новых пользователей (добавленных администратором или рекрутером) и изменённые профили.
        Новый пользователь приступает к обязанностям только после подтверждения. Откройте профиль, сверьте данные с документами и
        подтвердите или верните на доработку.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {!list.data ? <Pending /> : list.data.items.length === 0 ? <Card><Empty>Все профили проверены</Empty></Card> : (
        <Card>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Пользователь</th><th>Логин</th><th>Роли</th><th>Анкета</th><th>Добавил</th><th>Создан</th><th>Решение</th></tr></thead>
              <tbody>{list.data.items.map((u) => (
                <tr key={u.id}>
                  <td><Link to={`/people/${u.id}`}><strong>{u.display_name}</strong></Link>
                    <div><Link className="btn small" to={`/people/${u.id}`}>Открыть профиль</Link></div></td>
                  <td className="small">{u.username}</td>
                  <td className="small">{u.roles.map((r) => ROLE_LABELS[r.role] ?? r.role).join(", ") || "без ролей"}</td>
                  <td>{u.profile_verified === false
                    ? <span className="verify-flag bad">новая — не подтверждена</span>
                    : <span className="badge tone-warn">изменён профиль</span>}</td>
                  <td className="small">{u.registered_by ?? "вход из домена"}</td>
                  <td className="small">{fmtDate(u.created_at)}</td>
                  <td><ProfileReview u={u} setMsg={setMsg} onDone={list.reload} compact /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

export function RecruiterOperatorsPage() {
  const ops = useLive(() => api.get<{ items: OperatorRow[] }>("/api/v1/admin/operators"), [],
    (m) => m.kind.startsWith("notification"));
  const [msg, setMsg] = useState<Msg>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const decide = (o: OperatorRow, approve: boolean) => run(setMsg,
    () => api.post(`/api/v1/admin/operators/${o.user_id}/decision`, { approve, notes: notes[o.user_id] ?? "" }),
    approve ? `${o.display_name} допущен к работе оператором` : "Заявка отклонена", ops.reload);
  const suspend = (o: OperatorRow) => run(setMsg,
    () => api.post(`/api/v1/admin/operators/${o.user_id}/suspend`, { approve: false, notes: notes[o.user_id] || "Приостановлен" }),
    "Допуск приостановлен", ops.reload);
  return (
    <div className="stack">
      <h1>Допуск операторов</h1>
      <p className="muted">Проверьте номер свидетельства (лицензии) оператора и его квалификацию. Решение записывается в журнал аудита.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title="Заявки">
        {!ops.data ? <Pending /> : ops.data.items.length === 0 ? <Empty>Заявок нет</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Оператор</th><th>Свидетельство</th><th>Статус</th><th>Комментарий</th><th>Решение</th></tr></thead>
            <tbody>{ops.data.items.map((o) => (
              <tr key={o.user_id}>
                <td><strong>{o.display_name}</strong><div className="muted small">{o.username} · {fmtDate(o.created_at)}</div></td>
                <td className="mono">{o.license_number}{o.notes && <div className="muted small">{o.notes}</div>}</td>
                <td><Tone tone={o.status === "approved" ? "ok" : o.status === "pending" ? "warn" : "danger"}>{OP_LABEL[o.status] ?? o.status}</Tone></td>
                <td><input value={notes[o.user_id] ?? ""} onChange={(e) => setNotes({ ...notes, [o.user_id]: e.target.value })}
                           placeholder="Комментарий к решению" aria-label={`Комментарий к заявке ${o.display_name}`} /></td>
                <td className="row wrap">
                  {o.status !== "approved" && <button className="btn small primary" onClick={() => void decide(o, true)}>Допустить</button>}
                  {o.status === "pending" && <button className="btn small" onClick={() => void decide(o, false)}>Отклонить</button>}
                  {o.status === "approved" && <button className="btn small danger" onClick={() => void suspend(o)}>Приостановить</button>}
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
