import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { Alert, Card, KV, Pending, Tone } from "../../components/ui";
import { ROLE_LABELS, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { PROFILE_LABEL, PROFILE_TONE } from "../Profile";
import { GroupSelect } from "./GroupSelect";
import { STATUS_TONE, loadUnits, type PersonCard, type RoleRow } from "./common";

type Msg = { tone: string; text: string } | null;

/** Карточка пользователя: анкета, группа, должности (роли), учётная запись, статус. */
export function PersonCardPage() {
  const { id = "" } = useParams();
  const { can, me } = useAuth();
  const card = useLive(() => api.get<PersonCard>(`/api/v1/people/${id}`), [id]);
  const units = useLive(loadUnits, []);
  const roles = useLive(() => api.get<{ items: RoleRow[] }>("/api/v1/recruitment/roles"), []);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (card.error) return <Alert>{errorText(card.error)}</Alert>;
  if (!card.data) return <Pending />;
  const c = card.data;
  const self = c.id === me?.id;

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "ok", text: ok });
      card.reload();
      return true;
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
      return false;
    } finally {
      setBusy(false);
    }
  }
  const assignable = roles.data?.items ?? [];
  const has = new Set(c.roles.map((r) => r.role));

  return (
    <div className="stack">
      <div className="row between">
        <div>
          <h1>{c.display_name} <Tone tone={STATUS_TONE[c.status] ?? "muted"}>{c.status_label}</Tone></h1>
          <div className="muted small">{c.username} · {c.auth_source === "local" ? "учётная запись приложения" : "доменная учётная запись"}
            {c.registered_by && <> · добавил(а) {c.registered_by.display_name}</>}{c.created_at && <>, {fmtDate(c.created_at)}</>}</div>
        </div>
        <Link to="/people" className="btn ghost">← Ко всем пользователям</Link>
      </div>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {c.status !== "active" && (
        <Alert tone={c.status === "banned" ? "danger" : "warn"}>
          Учётная запись {c.status_label}{c.status_reason && <>: {c.status_reason}</>}
          {c.status_changed_by && <> ({c.status_changed_by.display_name}, {fmtDate(c.status_changed_at)})</>}.
          {c.status === "suspended" && " Вход возможен, должностные обязанности недоступны."}
          {c.status === "banned" && " При входе пользователь видит «Ваш аккаунт забанен. Обратитесь в техподдержку»."}
        </Alert>
      )}
      {can("user.recruit") && (
        <VerifyCard c={c} self={self} busy={busy}
                    onReview={(approve, notes) => run(() => api.post(`/api/v1/recruitment/users/${c.id}/profile`, { approve, notes }),
                      approve ? "Профиль подтверждён" : "Профиль возвращён на доработку")} />
      )}
      <div className="grid2">
        <ProfileCard c={c} canEdit={can("user.profile.edit")} canGroup={can("user.recruit") || can("user.profile.edit")}
                     units={units.data?.items ?? []} busy={busy}
                     onSave={(body, ok) => run(() => api.patch(`/api/v1/people/${c.id}/profile`, body), ok)} />
        <div className="stack">
          <Card title="Должность в системе (роли)">
            <div className="role-chips">
              {c.roles.map((r) => (
                <span key={`${r.role}-${r.source}`} className={`chip ${r.source}`}
                      title={r.source === "domain" ? "Из доменной группы — меняется в домене"
                        : `Выдал(а): ${r.granted_by?.display_name ?? "—"}, ${fmtDate(r.granted_at)}`}>
                  {ROLE_LABELS[r.role] ?? r.role}
                  {r.source === "manual" && !self && assignable.some((a) => a.code === r.role) && (
                    <button type="button" aria-label={`Снять роль ${ROLE_LABELS[r.role] ?? r.role}`} disabled={busy}
                            onClick={() => confirm(`Снять роль «${ROLE_LABELS[r.role] ?? r.role}»?`) &&
                              void run(() => api.post(`/api/v1/recruitment/users/${c.id}/roles`, { role: r.role, grant: false }), "Роль снята")}>×</button>
                  )}
                </span>
              ))}
              {c.roles.length === 0 && <span className="muted small">ролей нет — вход невозможен</span>}
            </div>
            {c.roles.filter((r) => r.role === "admin" && r.source === "manual").map((r) => (
              <p key="adm" className="small">Администратор: назначил(а) <b>{r.granted_by?.display_name ?? "—"}</b>, {fmtDate(r.granted_at)}</p>
            ))}
            {!self && (
              <select value="" disabled={busy} aria-label="Выдать роль"
                      onChange={(e) => e.target.value && void run(() => api.post(`/api/v1/recruitment/users/${c.id}/roles`,
                        { role: e.target.value, grant: true }), "Роль выдана")}>
                <option value="">+ выдать роль…</option>
                {assignable.filter((r) => !has.has(r.code)).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
              </select>
            )}
          </Card>
          <AccountCard c={c} busy={busy} self={self}
                       canCredentials={can("user.credentials")} canStatus={can("user.status")} run={run} />
        </div>
      </div>
      <ProfileHistory id={c.id} version={`${c.profile_status}|${c.status}|${c.roles.length}|${c.password_changed_at}`} />
    </div>
  );
}

function ProfileCard({ c, canEdit, canGroup, units, busy, onSave }: {
  c: PersonCard; canEdit: boolean; canGroup: boolean; units: { id: string; parent_id: string | null; name: string; path: string; members: number }[];
  busy: boolean; onSave: (body: Record<string, unknown>, ok: string) => Promise<boolean>;
}) {
  const [f, setF] = useState({ ...Object.fromEntries(Object.entries(c.profile).map(([k, v]) => [k, v ?? ""])) } as Record<string, string>);
  const [unit, setUnit] = useState(c.org_unit?.id ?? "");
  const fields: [string, string][] = [["last_name", "Фамилия"], ["first_name", "Имя"], ["middle_name", "Отчество"],
    ["nickname", "Никнейм"], ["position_title", "Должность в организации"], ["location", "Местоположение"], ["phone", "Телефон"], ["email", "Почта"], ["profile_notes", "Примечание"]];
  function save(e: FormEvent) {
    e.preventDefault();
    void onSave(f, "Анкета сохранена");
  }
  return (
    <Card title="Анкета">
      {canEdit ? (
        <form className="form grid2" onSubmit={save}>
          {fields.map(([k, label]) => (
            <label key={k} className={k === "profile_notes" ? "span2" : ""}>{label}
              <input value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
          ))}
          <div className="span2"><button className="btn primary" disabled={busy}>Сохранить анкету</button></div>
        </form>
      ) : (
        <KV items={fields.map(([k, label]) => [label, (c.profile as Record<string, string | null>)[k] || "—"])} />
      )}
      <div className="stack group-edit">
        <strong>Группа / подгруппа</strong>
        {canGroup ? (
          <div className="row wrap">
            <GroupSelect units={units} value={unit} onChange={setUnit} />
            <button className="btn" disabled={busy || unit === (c.org_unit?.id ?? "")}
                    onClick={() => void onSave({ org_unit_id: unit || null }, "Группа изменена")}>Сохранить группу</button>
          </div>
        ) : <span>{c.org_unit?.path ?? "—"}</span>}
        {c.org_unit && <span className="muted small">{c.org_unit.path}</span>}
      </div>
      <KV items={[
        ["Проверка профиля", <Tone tone={PROFILE_TONE[c.profile_status] ?? "muted"}>{PROFILE_LABEL[c.profile_status] ?? c.profile_status}</Tone>],
        ["Допуск оператора", c.operator ? `${c.operator.status}, свидетельство ${c.operator.license_number}` : "—"],
        ["Последний вход", fmtDate(c.last_login_at)],
      ]} />
    </Card>
  );
}

function AccountCard({ c, busy, self, canCredentials, canStatus, run }: {
  c: PersonCard; busy: boolean; self: boolean; canCredentials: boolean; canStatus: boolean;
  run: (fn: () => Promise<unknown>, ok: string) => Promise<boolean>;
}) {
  const [login, setLogin] = useState(c.username);
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const mismatch = !!password && password !== password2;
  const [reason, setReason] = useState("");
  const status = (s: string, ok: string) => run(() => api.post(`/api/v1/people/${c.id}/status`, { status: s, reason }), ok)
    .then((done) => done && setReason(""));
  return (
    <Card title="Учётная запись">
      <KV items={[
        ["Вход", c.auth_source === "local" ? "логин и пароль приложения" : "пароль домена"],
        ["Логин", c.username],
        ["Пароль изменён", c.auth_source === "local"
          ? `${fmtDate(c.password_changed_at)}${c.password_changed_by ? ` — ${c.password_changed_by.display_name}` : ""}` : "—"],
        ["Статус", <Tone tone={STATUS_TONE[c.status] ?? "muted"}>{c.status_label}</Tone>],
      ]} />
      {canCredentials && c.auth_source === "local" && (
        <form className="form grid2" onSubmit={(e) => { e.preventDefault();
          void run(() => api.post(`/api/v1/people/${c.id}/credentials`, {
            ...(login !== c.username ? { username: login } : {}), ...(password ? { password, password_confirm: password2 } : {}) }),
            "Данные для входа изменены, сессии пользователя завершены").then((ok) => { if (ok) { setPassword(""); setPassword2(""); } }); }}>
          <label>Новый логин<input value={login} onChange={(e) => setLogin(e.target.value)} minLength={3} maxLength={64} /></label>
          <label>Новый пароль<input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                                    minLength={10} autoComplete="new-password" placeholder="не менять" /></label>
          {password && <label className="span2">Повторите новый пароль<input type="password" value={password2}
            onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" required aria-invalid={mismatch} />
            {password2 && mismatch && <span className="small danger-text">Пароли не совпадают</span>}</label>}
          <div className="span2"><button className="btn" disabled={busy || (login === c.username && !password) || mismatch}>Сменить логин/пароль</button></div>
        </form>
      )}
      {c.auth_source === "domain" && canCredentials && <p className="muted small">Пароль доменной учётной записи меняется в домене.</p>}
      {canStatus && !self && (
        <div className="stack">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Причина (обязательна для бана и приостановки)"
                 aria-label="Причина смены статуса" maxLength={500} />
          <div className="row wrap">
            {c.status !== "suspended" && <button className="btn" disabled={busy || reason.trim().length < 3}
              onClick={() => void status("suspended", "Учётная запись приостановлена")}>Приостановить</button>}
            {c.status !== "banned" && <button className="btn danger" disabled={busy || reason.trim().length < 3}
              onClick={() => confirm(`Забанить ${c.display_name}? Вход будет запрещён, сессии завершены.`) &&
                void status("banned", "Учётная запись забанена")}>Забанить</button>}
            {(c.status === "banned" || c.status === "suspended") && <button className="btn primary" disabled={busy}
              onClick={() => void status("active", c.status === "banned" ? "Учётная запись разбанена" : "Действие учётной записи возобновлено")}>
              {c.status === "banned" ? "Разбанить" : "Возобновить"}</button>}
          </div>
        </div>
      )}
    </Card>
  );
}

/** Подтверждение профиля: новый пользователь приступает к обязанностям только после него. */
function VerifyCard({ c, self, busy, onReview }: {
  c: PersonCard; self: boolean; busy: boolean; onReview: (approve: boolean, notes: string) => Promise<boolean>;
}) {
  const [notes, setNotes] = useState("");
  const verified = c.profile_verified !== false;
  return (
    <Card title={<>Проверка профиля{" "}{verified
      ? <span className="verify-flag good">Учётная запись подтверждена</span>
      : <span className="verify-flag bad">Учётная запись не подтверждена</span>}</>}>
      <p className="small">Статус анкеты: <Tone tone={PROFILE_TONE[c.profile_status] ?? "muted"}>{PROFILE_LABEL[c.profile_status] ?? c.profile_status}</Tone>
        {c.profile_review_notes && <> — {c.profile_review_notes}</>}
        {!verified && " Пока профиль не подтверждён, рабочие разделы пользователю недоступны."}</p>
      {!self && c.profile_status !== "verified" && (
        <div className="row wrap">
          <button className="btn ok-fill" disabled={busy} onClick={() => void onReview(true, "")}>✓ Подтвердить профиль</button>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Что исправить (для возврата)"
                 aria-label="Что исправить в профиле" maxLength={500} />
          <button className="btn" disabled={busy || notes.trim().length < 5}
                  onClick={() => void onReview(false, notes).then((ok) => ok && setNotes(""))}>Вернуть на доработку</button>
        </div>
      )}
      {self && <p className="muted small">Собственный профиль подтверждает другой сотрудник.</p>}
    </Card>
  );
}

interface HistoryRow { id: number; at: string; action: string; label: string; actor: string; outcome: string; details: Record<string, unknown> }

const HIDDEN_KEYS = new Set(["before", "after"]);
function describe(d: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    if (v == null || v === "" || HIDDEN_KEYS.has(k)) continue;
    parts.push(`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  }
  if (d.before !== undefined || d.after !== undefined) parts.push(`было: ${JSON.stringify(d.before)} → стало: ${JSON.stringify(d.after)}`);
  return parts.join("; ");
}

/** История изменения профиля — все действия с учётной записью от создания (журнал аудита). */
function ProfileHistory({ id, version }: { id: string; version: string }) {
  const h = useLive(() => api.get<{ items: HistoryRow[] }>(`/api/v1/people/${id}/history`), [id, version]);
  return (
    <Card title="История изменения профиля">
      {h.error ? <Alert>{errorText(h.error)}</Alert> : !h.data ? <Pending /> : h.data.items.length === 0 ? <p className="muted">Записей нет</p> : (
        <div className="table-wrap">
          <table className="table compact">
            <thead><tr><th>Когда</th><th>Действие</th><th>Кто</th><th>Подробности</th></tr></thead>
            <tbody>{[...h.data.items].reverse().map((r) => (
              <tr key={r.id}><td className="small">{fmtDate(r.at)}</td><td>{r.label}</td><td className="small">{r.actor}</td>
                <td className="small history-details">{describe(r.details)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
