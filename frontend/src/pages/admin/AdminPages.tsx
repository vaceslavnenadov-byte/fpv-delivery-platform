import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorText } from "../../api";
import { Alert, Badge, Card, Empty, KV, Pending, Stat, Tone } from "../../components/ui";
import { ORDER_STATUS, ROLE_LABELS, fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { invalidateMaps } from "../../maps";
import type { Drone, Fleet, MapLayer, Order, Video } from "../../types";
import { OrderTable } from "../../components/OrderTable";
import { DroneService } from "../../components/DroneService";

type Msg = { tone: string; text: string } | null;

async function run(setMsg: (m: Msg) => void, fn: () => Promise<unknown>, ok: string, after?: () => void) {
  try {
    await fn();
    setMsg({ tone: "ok", text: ok });
    after?.();
  } catch (err) {
    setMsg({ tone: "danger", text: errorText(err) });
  }
}

// --- Обзор и отчёты ---------------------------------------------------------

interface Stats {
  orders_by_status: Record<string, number>;
  orders_total: number;
  drones_by_health: Record<string, number>;
  video_by_status: Record<string, number>;
  average_final_score: number | null;
  top_operators: { name: string; points: number; completed: number }[];
}

export function AdminOverviewPage() {
  const s = useLive(() => api.get<Stats>("/api/v1/admin/stats"), [], (m) => m.kind.startsWith("order."));
  if (!s.data) return <Pending />;
  const d = s.data;
  const max = Math.max(1, ...Object.values(d.orders_by_status));
  return (
    <div className="stack">
      <h1>Обзор</h1>
      <div className="stats">
        <Stat label="Всего заказов" value={d.orders_total} />
        <Stat label="На проверке" value={d.orders_by_status.admin_review ?? 0} />
        <Stat label="Споры" value={d.orders_by_status.disputed ?? 0} />
        <Stat label="Средняя оценка" value={d.average_final_score ?? "—"} />
        <Stat label="Дронов требуют проверки" value={(d.drones_by_health.requires_check ?? 0) + (d.drones_by_health.grounded ?? 0)} />
      </div>
      <div className="grid2">
        <Card title="Заказы по статусам">
          <div className="bars">
            {Object.entries(ORDER_STATUS).filter(([k]) => d.orders_by_status[k]).map(([k, v]) => (
              <div key={k} className="bar-row">
                <span className="bar-label">{v.label}</span>
                <span className="bar"><span className={`bar-fill tone-${v.tone}`} style={{ width: `${(100 * d.orders_by_status[k]) / max}%` }} /></span>
                <span className="num">{d.orders_by_status[k]}</span>
              </div>
            ))}
            {d.orders_total === 0 && <Empty>Заказов пока нет</Empty>}
          </div>
        </Card>
        <Card title="Лучшие операторы">
          {d.top_operators.length === 0 ? <Empty>Нет данных</Empty> : (
            <table className="table compact"><thead><tr><th>Оператор</th><th>Баллы</th><th>Выполнено</th></tr></thead>
              <tbody>{d.top_operators.map((o) => <tr key={o.name}><td>{o.name}</td><td className="num">{o.points}</td><td className="num">{o.completed}</td></tr>)}</tbody></table>
          )}
          <KV items={Object.entries(d.video_by_status).map(([k, v]) => [`Видео: ${k}`, v])} />
        </Card>
      </div>
    </div>
  );
}

// --- Проверка выполнения ----------------------------------------------------

export function AdminReviewsPage() {
  const q = useLive(() => api.get<{ items: Order[] }>("/api/v1/orders?scope=all&status=admin_review"), [], (m) => m.kind.startsWith("order.") || m.kind === "video.stored");
  const videos = useLive(() => api.get<{ items: Video[] }>("/api/v1/admin/videos?review_status=pending"), [], (m) => m.kind === "video.stored");
  return (
    <div className="stack">
      <h1>Проверка выполнения</h1>
      <Card title="Заказы на проверке администратора">{q.data ? <OrderTable orders={q.data.items} /> : <Pending />}</Card>
      <Card title="Видеоподтверждения, ожидающие проверки">
        {!videos.data ? <Pending /> : videos.data.items.length === 0 ? <Empty>Нет</Empty> : (
          <table className="table"><thead><tr><th>Заказ</th><th>Статус</th><th>Событие завершения</th><th>Сегменты</th><th>Целостность</th><th>SHA-256</th></tr></thead>
            <tbody>{videos.data.items.map((v) => (
              <tr key={v.id}><td><Link to={`/orders/${v.order_id}`}>открыть</Link></td>
                <td><Tone tone={v.status === "stored" ? "ok" : v.status === "missing" ? "danger" : "warn"}>{v.status}</Tone>{v.missing_reason && <div className="small">{v.missing_reason}</div>}</td>
                <td className="small">{fmtDate(v.completion_event_at)}</td><td className="num">{v.segment_count}</td>
                <td>{v.integrity_ok == null ? "—" : v.integrity_ok ? "OK" : "НАРУШЕНА"}</td>
                <td className="mono small">{v.sha256?.slice(0, 16) ?? "—"}…</td></tr>
            ))}</tbody></table>
        )}
      </Card>
    </div>
  );
}

export function AdminOrdersPage() {
  const [status, setStatus] = useState("");
  const q = useLive(() => api.get<{ items: Order[] }>(`/api/v1/orders?scope=all${status ? `&status=${status}` : ""}`), [status],
    (m) => m.kind.startsWith("order."));
  return (
    <div className="stack">
      <h1>Все заказы</h1>
      <Card title="Заказы" actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          {Object.entries(ORDER_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>}>
        {q.data ? <OrderTable orders={q.data.items} showCustomer /> : <Pending />}
      </Card>
    </div>
  );
}

// --- Дроны -------------------------------------------------------------------

export function AdminDronesPage() {
  const list = useLive(() => api.get<{ items: Drone[] }>("/api/v1/drones?scope=all"), [], (m) => m.kind.startsWith("drone."));
  const fleets = useLive(() => api.get<{ items: Fleet[] }>("/api/v1/fleets"), []);
  const fleetName = (id?: string | null) => fleets.data?.items.find((f) => f.id === id)?.name;
  const moveTo = (d: Drone, fleetId: string) => {
    if (!fleetId || !confirm(`Перевести дрон ${d.registration_number} в парк «${fleetName(fleetId)}»? Он будет закрыт для заказов до решения техника.`)) return;
    void run(setMsg, () => api.post(`/api/v1/drones/${d.id}/fleet`, { fleet_id: fleetId }), "Дрон переведён в парк", list.reload);
  };
  const [msg, setMsg] = useState<Msg>(null);
  const [sel, setSel] = useState<string | null>(null);
  const decide = (d: Drone, action: string) => {
    const reason = action === "block" ? prompt("Причина блокировки") ?? "" : "";
    if (action === "block" && !reason) return;
    void run(setMsg, () => api.post(`/api/v1/drones/${d.id}/decision`, { action, reason }), "Решение сохранено", list.reload);
  };
  return (
    <div className="stack">
      <h1>Дроны</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title="Реестр дронов">
        {!list.data ? <Pending /> : (
          <table className="table"><thead><tr><th>Учётный номер</th><th>Модель</th><th>Парк</th><th>Регистрация</th><th>Техсостояние</th><th>Связь</th><th>Действия</th></tr></thead>
            <tbody>{list.data.items.map((d) => (
              <tr key={d.id}>
                <td><button className="link" onClick={() => setSel(d.id)}>{d.registration_number}</button><div className="muted small">S/N {d.serial_number}</div></td>
                <td>{d.manufacturer} {d.model}</td>
                <td>
                  <select value={d.fleet_id ?? ""} onChange={(e) => moveTo(d, e.target.value)} aria-label="Парк дрона">
                    <option value="" disabled>{d.fleet_id ? "—" : "без парка"}</option>
                    {(fleets.data?.items ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                  </select>
                  {d.published && <div className="small muted">открыт для заказов</div>}
                </td>
                <td><Badge kind="drone" value={d.status} />{d.blocked_reason && <div className="small">{d.blocked_reason}</div>}</td>
                <td><Badge kind="health" value={d.health_status} /></td>
                <td><Badge kind="link" value={d.link_status} /></td>
                <td className="row">
                  {d.status === "pending_approval" && <button className="btn small primary" onClick={() => decide(d, "approve")}>Подтвердить</button>}
                  {d.status === "active" && <button className="btn small danger" onClick={() => decide(d, "block")}>Заблокировать</button>}
                  {d.status === "blocked" && <button className="btn small" onClick={() => decide(d, "unblock")}>Разблокировать</button>}
                </td>
              </tr>
            ))}</tbody></table>
        )}
      </Card>
      {sel && <DroneService id={sel} />}
    </div>
  );
}

// --- Жалобы ------------------------------------------------------------------

interface Complaint {
  id: number; order_id: string; kind: string; status: string; description: string; resolution: string | null;
  points_adjustment: number | null; author: { display_name: string } | null; created_at: string;
}

export function AdminComplaintsPage() {
  const [status, setStatus] = useState("open");
  const list = useLive(() => api.get<{ items: Complaint[] }>(`/api/v1/admin/complaints${status ? `?status=${status}` : ""}`), [status],
    (m) => m.kind.startsWith("order.") || m.kind.startsWith("score."));
  const [msg, setMsg] = useState<Msg>(null);
  return (
    <div className="stack">
      <h1>Жалобы, споры и апелляции</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title="Обращения" actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open">Открытые</option><option value="upheld">Удовлетворённые</option><option value="dismissed">Отклонённые</option><option value="">Все</option>
        </select>}>
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Нет обращений</Empty> : (
          <div className="stack">
            {list.data.items.map((c) => <ComplaintItem key={c.id} c={c} onDone={list.reload} setMsg={setMsg} />)}
          </div>
        )}
      </Card>
    </div>
  );
}

function ComplaintItem({ c, onDone, setMsg }: { c: Complaint; onDone: () => void; setMsg: (m: Msg) => void }) {
  const [resolution, setResolution] = useState("");
  const [points, setPoints] = useState("");
  const resolve = (upheld: boolean) => run(setMsg, () => api.post(`/api/v1/admin/complaints/${c.id}/resolve`, {
    upheld, resolution, points_adjustment: points ? Number(points) : null,
  }), "Решение сохранено и записано в журнал аудита", onDone);
  return (
    <article className="complaint">
      <div className="row between">
        <strong>{c.kind === "dispute" ? "Спор по доставке" : "Апелляция оценки"} #{c.id}</strong>
        <span className="muted small">{c.author?.display_name} · {fmtDate(c.created_at)} · <Link to={`/orders/${c.order_id}`}>заказ</Link></span>
      </div>
      <p>{c.description}</p>
      {c.status === "open" ? (
        <div className="form row">
          <input className="grow" placeholder="Решение и обоснование (обязательно)" value={resolution} onChange={(e) => setResolution(e.target.value)} />
          <input type="number" placeholder="± баллы" value={points} onChange={(e) => setPoints(e.target.value)} style={{ width: 100 }} />
          <button className="btn primary" disabled={resolution.length < 5} onClick={() => resolve(true)}>Удовлетворить</button>
          <button className="btn" disabled={resolution.length < 5} onClick={() => resolve(false)}>Отклонить</button>
        </div>
      ) : <p className="small"><Tone tone={c.status === "upheld" ? "ok" : "muted"}>{c.status}</Tone> {c.resolution} {c.points_adjustment != null && `(${c.points_adjustment} б.)`}</p>}
    </article>
  );
}

// --- Пользователи, роли, группы, операторы, устройства ------------------------

interface UserRow {
  id: string; username: string; display_name: string; status: string; directory_enabled: boolean;
  roles: { role: string; source: string }[]; operator_status: string | null; last_login_at: string | null;
}
interface Group { id: number; dn: string; name: string; role: string | null; description: string }
interface OperatorRow { user_id: string; display_name: string; status: string; license_number: string; total_points: number; created_at: string }
interface DeviceRow { id: string; code: string; kind: string; hostname: string | null; domain_joined: boolean; status: string;
  policies: { id: number; role: string | null; domain_group: string | null; access_level: string }[] }
interface RolesData { roles: { code: string; name: string; permissions: string[] }[]; permissions: { code: string; description: string }[] }

export function AdminUsersPage() {
  const [q, setQ] = useState("");
  const users = useLive(() => api.get<{ items: UserRow[] }>(`/api/v1/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  const groups = useLive(() => api.get<{ items: Group[] }>("/api/v1/admin/domain-groups"), []);
  const ops = useLive(() => api.get<{ items: OperatorRow[] }>("/api/v1/admin/operators"), []);
  const devices = useLive(() => api.get<{ items: DeviceRow[] }>("/api/v1/admin/devices"), []);
  const roles = useLive(() => api.get<RolesData>("/api/v1/admin/roles"), []);
  const [msg, setMsg] = useState<Msg>(null);
  const [g, setG] = useState({ name: "", dn: "", role: "" });
  const [dev, setDev] = useState({ code: "", kind: "workstation", hostname: "", domain_joined: true });
  const [pol, setPol] = useState<Record<string, string>>({});

  const setStatus = (u: UserRow, status: string) => {
    const reason = prompt(`Причина (${status})`) ?? "";
    if (reason.length < 3) return;
    void run(setMsg, () => api.post(`/api/v1/admin/users/${u.id}/status`, { status, reason }), "Статус изменён, сессии отозваны", users.reload);
  };
  const grant = (u: UserRow, role: string, grantIt: boolean) =>
    run(setMsg, () => api.post(`/api/v1/admin/users/${u.id}/roles`, { role, grant: grantIt }), "Роль изменена", users.reload);

  return (
    <div className="stack">
      <h1>Пользователи, роли и доступ</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title="Пользователи" actions={<>
        <input placeholder="Поиск" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn small" onClick={() => run(setMsg, () => api.post("/api/v1/admin/directory/sync"), "Синхронизация с каталогом выполнена", users.reload)}>Синхронизировать с доменом</button>
      </>}>
        {!users.data ? <Pending /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Пользователь</th><th>Роли (источник)</th><th>Домен</th><th>Доступ</th><th>Последний вход</th><th>Действия</th></tr></thead>
            <tbody>{users.data.items.map((u) => (
              <tr key={u.id}>
                <td><strong>{u.display_name}</strong><div className="muted small">{u.username}</div></td>
                <td className="small">{u.roles.map((r) => `${ROLE_LABELS[r.role] ?? r.role} (${r.source === "domain" ? "группа" : "вручную"})`).join(", ") || "—"}
                  {u.operator_status && <div>Оператор: {u.operator_status}</div>}</td>
                <td>{u.directory_enabled ? <Tone tone="ok">активна</Tone> : <Tone tone="danger">заблокирована</Tone>}</td>
                <td><Tone tone={u.status === "active" ? "ok" : "danger"}>{u.status}</Tone></td>
                <td className="small">{fmtDate(u.last_login_at)}</td>
                <td className="row wrap">
                  {u.status === "active" ? <button className="btn small danger" onClick={() => setStatus(u, "disabled")}>Отключить</button>
                    : u.status === "disabled" && <button className="btn small" onClick={() => setStatus(u, "active")}>Включить</button>}
                  <select defaultValue="" onChange={(e) => { const [r, a] = e.target.value.split(":"); if (r) void grant(u, r, a === "1"); e.target.value = ""; }}>
                    <option value="">Роль вручную…</option>
                    {Object.keys(ROLE_LABELS).map((r) => <option key={`${r}1`} value={`${r}:1`}>+ {ROLE_LABELS[r]}</option>)}
                    {Object.keys(ROLE_LABELS).map((r) => <option key={`${r}0`} value={`${r}:0`}>− {ROLE_LABELS[r]}</option>)}
                  </select>
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>

      <div className="grid2">
        <Card title="Заявки операторов">
          {!ops.data ? <Pending /> : ops.data.items.length === 0 ? <Empty>Нет заявок</Empty> : (
            <table className="table compact"><tbody>{ops.data.items.map((o) => (
              <tr key={o.user_id}><td>{o.display_name}<div className="muted small">№ {o.license_number}</div></td>
                <td><Tone tone={o.status === "approved" ? "ok" : o.status === "pending" ? "warn" : "danger"}>{o.status}</Tone></td>
                <td className="row">
                  {o.status !== "approved" && <button className="btn small primary" onClick={() => run(setMsg, () => api.post(`/api/v1/admin/operators/${o.user_id}/decision`, { approve: true }), "Оператор допущен", () => { ops.reload(); users.reload(); })}>Допустить</button>}
                  {o.status === "pending" && <button className="btn small" onClick={() => run(setMsg, () => api.post(`/api/v1/admin/operators/${o.user_id}/decision`, { approve: false }), "Заявка отклонена", ops.reload)}>Отклонить</button>}
                  {o.status === "approved" && <button className="btn small danger" onClick={() => run(setMsg, () => api.post(`/api/v1/admin/operators/${o.user_id}/suspend`, { approve: false, notes: "Приостановлен" }), "Допуск приостановлен", ops.reload)}>Приостановить</button>}
                </td></tr>
            ))}</tbody></table>
          )}
        </Card>
        <Card title="Сопоставление доменных групп и ролей">
          {groups.data && (
            <table className="table compact"><tbody>{groups.data.items.map((x) => (
              <tr key={x.id}><td><strong>{x.name}</strong><div className="muted small mono">{x.dn}</div></td><td>{x.role ? ROLE_LABELS[x.role] : "—"}</td></tr>
            ))}</tbody></table>
          )}
          <form className="form grid2" onSubmit={(e: FormEvent) => { e.preventDefault(); void run(setMsg, () => api.post("/api/v1/admin/domain-groups", { ...g, role: g.role || null }), "Сопоставление сохранено", groups.reload); }}>
            <label>Имя группы<input value={g.name} onChange={(e) => setG({ ...g, name: e.target.value })} required /></label>
            <label>Роль<select value={g.role} onChange={(e) => setG({ ...g, role: e.target.value })}>
              <option value="">— без роли —</option>{Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label className="span2">DN группы<input value={g.dn} onChange={(e) => setG({ ...g, dn: e.target.value })} required placeholder="CN=...,OU=...,DC=..." /></label>
            <div><button className="btn">Сохранить</button></div>
          </form>
        </Card>
      </div>

      <Card title="Устройства и политики доступа">
        {devices.data && devices.data.items.length > 0 && (
          <table className="table"><thead><tr><th>Код</th><th>Тип</th><th>Хост</th><th>Домен</th><th>Разрешено</th><th>Добавить правило</th></tr></thead>
            <tbody>{devices.data.items.map((d) => (
              <tr key={d.id}><td><strong>{d.code}</strong></td><td>{d.kind}</td><td className="small">{d.hostname}</td>
                <td>{d.domain_joined ? "в домене" : "нет"}</td>
                <td className="small">{d.policies.map((p) => (
                  <span key={p.id} className="chip">{p.role ? ROLE_LABELS[p.role] : p.domain_group} ({p.access_level})
                    <button className="link" aria-label="Удалить" onClick={() => run(setMsg, () => api.del(`/api/v1/admin/device-policies/${p.id}`), "Правило удалено", devices.reload)}>×</button></span>
                ))}</td>
                <td className="row">
                  <select value={pol[d.id] ?? ""} onChange={(e) => setPol({ ...pol, [d.id]: e.target.value })}>
                    <option value="">роль / группа…</option>
                    {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={`role:${k}`}>Роль: {v}</option>)}
                    {(groups.data?.items ?? []).map((x) => <option key={x.id} value={`group:${x.name}`}>Группа: {x.name}</option>)}
                  </select>
                  <button className="btn small" disabled={!pol[d.id]} onClick={() => {
                    const [t, v] = (pol[d.id] ?? "").split(":");
                    void run(setMsg, () => api.post(`/api/v1/admin/devices/${d.id}/policies`, t === "role" ? { role: v } : { domain_group: v }), "Правило добавлено", devices.reload);
                  }}>Добавить</button>
                </td></tr>
            ))}</tbody></table>
        )}
        <form className="form row" onSubmit={(e: FormEvent) => { e.preventDefault(); void run(setMsg, () => api.post("/api/v1/admin/devices", { ...dev, hostname: dev.hostname || null }), "Устройство зарегистрировано", devices.reload); }}>
          <label>Код<input value={dev.code} onChange={(e) => setDev({ ...dev, code: e.target.value })} required placeholder="WS-OPS-01" /></label>
          <label>Тип<select value={dev.kind} onChange={(e) => setDev({ ...dev, kind: e.target.value })}>
            <option value="workstation">Рабочая станция</option><option value="ground_station">Наземная станция</option><option value="server">Сервер</option><option value="mobile">Мобильное</option></select></label>
          <label>Хост<input value={dev.hostname} onChange={(e) => setDev({ ...dev, hostname: e.target.value })} /></label>
          <label className="check"><input type="checkbox" checked={dev.domain_joined} onChange={(e) => setDev({ ...dev, domain_joined: e.target.checked })} /> в домене</label>
          <button className="btn">Зарегистрировать устройство</button>
        </form>
      </Card>

      {roles.data && (
        <Card title="Таблица прав (роль → разрешения)">
          <div className="table-wrap"><table className="table compact matrix">
            <thead><tr><th>Разрешение</th>{roles.data.roles.map((r) => <th key={r.code}>{r.name}</th>)}</tr></thead>
            <tbody>{roles.data.permissions.map((p) => (
              <tr key={p.code}><td><span className="mono small">{p.code}</span><div className="muted small">{p.description}</div></td>
                {roles.data!.roles.map((r) => <td key={r.code} className="center">{r.permissions.includes(p.code) ? "●" : ""}</td>)}</tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}
    </div>
  );
}

// --- Правила баллов ----------------------------------------------------------

export function AdminScoringPage() {
  const r = useLive(() => api.get<{ version: number; rules: Record<string, Record<string, number>>; comment: string; created_at: string }>("/api/v1/admin/scoring-rules"), []);
  const [draft, setDraft] = useState<Record<string, Record<string, number>> | null>(null);
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  if (!r.data) return <Pending />;
  const rules = draft ?? r.data.rules;
  const set = (sec: string, key: string, val: string) => setDraft({ ...rules, [sec]: { ...rules[sec], [key]: Number(val) } });
  const total = rules.time_window.max_points + ["receipt_confirmed", "report_submitted", "video_verified", "admin_approved"]
    .reduce((s, k) => s + (rules.result[k] ?? 0), 0);
  const labels: Record<string, string> = {
    max_points: "Временное окно, макс.", grace_minutes: "Допуск отклонения, мин", receipt_confirmed: "Подтверждение получения",
    report_submitted: "Отчёт", video_verified: "Проверенное видео", admin_approved: "Подтверждение администратора",
    upheld_complaint_penalty: "Штраф за обоснованную претензию",
  };
  return (
    <div className="stack">
      <h1>Правила начисления баллов</h1>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <Card title={`Действующая версия ${r.data.version} · ${fmtDate(r.data.created_at)}`}>
        <div className="form grid3">
          {Object.entries(rules).flatMap(([sec, items]) => Object.entries(items).map(([k, v]) => (
            <label key={`${sec}.${k}`}>{labels[k] ?? k}<input type="number" min={0} value={v} onChange={(e) => set(sec, k, e.target.value)} /></label>
          )))}
        </div>
        <p className={total === 100 ? "muted" : "danger-text"}>Сумма максимальных баллов: {total} (должна быть 100)</p>
        <label>Обоснование изменения<input value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        <button className="btn primary" disabled={!draft || total !== 100} onClick={() => run(setMsg,
          () => api.put("/api/v1/admin/scoring-rules", { rules, comment }), "Новая версия правил сохранена (изменение записано в аудит)",
          () => { setDraft(null); r.reload(); })}>Сохранить новую версию</button>
        <p className="muted small">Уже начисленные баллы не пересчитываются: каждая транзакция хранит версию правил.</p>
      </Card>
    </div>
  );
}

// --- Журнал аудита -----------------------------------------------------------

interface AuditRow { id: number; occurred_at: string; actor: string; actor_ip: string | null; action: string; entity_type: string;
  entity_id: string; outcome: string; details: Record<string, unknown>; hash: string }

export function AdminAuditPage() {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const list = useLive(() => api.get<{ items: AuditRow[] }>(`/api/v1/admin/audit?limit=200${action ? `&action=${encodeURIComponent(action)}` : ""}${actor ? `&actor=${encodeURIComponent(actor)}` : ""}`), [action, actor]);
  const [verify, setVerify] = useState<{ ok: boolean; checked: number; broken_at: number | null } | null>(null);
  return (
    <div className="stack">
      <h1>Журнал аудита</h1>
      <Card title="Целостность журнала" actions={<button className="btn" onClick={async () => setVerify(await api.post("/api/v1/admin/audit/verify"))}>Проверить цепочку хешей</button>}>
        <p className="muted small">Журнал только для добавления (запрет UPDATE/DELETE на уровне БД); каждая запись содержит хеш предыдущей.</p>
        {verify && <Alert tone={verify.ok ? "ok" : "danger"}>{verify.ok ? `Цепочка цела: проверено ${verify.checked} записей` : `Нарушение целостности на записи #${verify.broken_at}`}</Alert>}
      </Card>
      <Card title="События" actions={<>
        <input placeholder="Действие (префикс), напр. auth." value={action} onChange={(e) => setAction(e.target.value)} />
        <input placeholder="Пользователь" value={actor} onChange={(e) => setActor(e.target.value)} />
      </>}>
        {!list.data ? <Pending /> : (
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>#</th><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Итог</th><th>Детали</th></tr></thead>
            <tbody>{list.data.items.map((a) => (
              <tr key={a.id}><td className="num">{a.id}</td><td className="small">{fmtDate(a.occurred_at)}</td>
                <td className="small">{a.actor}<div className="muted">{a.actor_ip}</div></td><td className="mono small">{a.action}</td>
                <td className="small">{a.entity_type} {a.entity_id.slice(0, 8)}</td>
                <td><Tone tone={a.outcome === "success" ? "ok" : a.outcome === "denied" ? "warn" : "danger"}>{a.outcome}</Tone></td>
                <td className="mono small details">{JSON.stringify(a.details)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}

// --- Мониторинг --------------------------------------------------------------

interface Monitoring {
  services: { name: string; ok: boolean; details: Record<string, unknown> }[];
  drone_links: Record<string, number>;
  active_sessions: number;
  connected_drones: { id: string; registration_number: string; link_status: string; battery_pct: number | null; last_seen_at: string | null }[];
  devices: { id: string; code: string; kind: string; hostname: string | null; domain_joined: boolean; status: string }[];
  checked_at: string;
}

function bytes(n: unknown): string {
  return typeof n === "number" ? `${(n / 1024 ** 3).toFixed(1)} ГБ` : String(n);
}

export function AdminMonitoringPage() {
  const m = useLive(() => api.get<Monitoring>("/api/v1/admin/monitoring"), [], (x) => x.kind === "drone.link");
  if (!m.data) return <Pending />;
  return (
    <div className="stack">
      <div className="row between"><h1>Мониторинг</h1><button className="btn" onClick={m.reload}>Обновить</button></div>
      <p className="muted small">Проверено {fmtDate(m.data.checked_at)} · активных сессий: {m.data.active_sessions}</p>
      <div className="cards">
        {m.data.services.map((s) => (
          <article key={s.name} className={`svc ${s.ok ? "ok" : "fail"}`}>
            <div className="row between"><strong>{s.name}</strong><Tone tone={s.ok ? "ok" : "danger"}>{s.ok ? "работает" : "сбой"}</Tone></div>
            <div className="small muted">{Object.entries(s.details).map(([k, v]) => `${k}: ${k.endsWith("bytes") ? bytes(v) : String(v)}`).join(" · ")}</div>
          </article>
        ))}
      </div>
      <div className="grid2">
        <Card title="Подключённые дроны">
          {m.data.connected_drones.length === 0 ? <Empty>Нет подключённых дронов</Empty> : (
            <table className="table compact"><tbody>{m.data.connected_drones.map((d) => (
              <tr key={d.id}><td>{d.registration_number}</td><td><Badge kind="link" value={d.link_status} /></td>
                <td className="num">{d.battery_pct ?? "—"}%</td><td className="small">{fmtDate(d.last_seen_at)}</td></tr>
            ))}</tbody></table>
          )}
        </Card>
        <Card title="Устройства сети">
          {m.data.devices.length === 0 ? <Empty>Устройства не зарегистрированы</Empty> : (
            <table className="table compact"><tbody>{m.data.devices.map((d) => (
              <tr key={d.id}><td>{d.code}</td><td>{d.kind}</td><td className="small">{d.hostname}</td>
                <td>{d.domain_joined ? <Tone tone="ok">в домене</Tone> : <Tone tone="warn">вне домена</Tone>}</td></tr>
            ))}</tbody></table>
          )}
        </Card>
      </div>
    </div>
  );
}

// --- Карты-подложки ---------------------------------------------------------

const EMPTY_BOUNDS = { north: "", south: "", west: "", east: "" };
type BoundsForm = typeof EMPTY_BOUNDS;

function boundsValid(b: BoundsForm): string {
  const [n, s, w, e] = [b.north, b.south, b.west, b.east].map(Number);
  if ([b.north, b.south, b.west, b.east].some((v) => v.trim() === "") || [n, s, w, e].some(Number.isNaN))
    return "Укажите все четыре границы числами (градусы, разделитель — точка)";
  if (!(s < n)) return "Северная граница должна быть больше южной";
  if (!(w < e)) return "Восточная граница должна быть больше западной";
  return "";
}

function BoundsInputs({ value, onChange }: { value: BoundsForm; onChange: (b: BoundsForm) => void }) {
  const set = (k: keyof BoundsForm) => (e: { target: { value: string } }) =>
    onChange({ ...value, [k]: e.target.value.replace(",", ".") });
  return (
    <div className="form grid4">
      <label>Север (широта)<input inputMode="decimal" value={value.north} onChange={set("north")} placeholder="55.80" required /></label>
      <label>Юг (широта)<input inputMode="decimal" value={value.south} onChange={set("south")} placeholder="55.70" required /></label>
      <label>Запад (долгота)<input inputMode="decimal" value={value.west} onChange={set("west")} placeholder="37.50" required /></label>
      <label>Восток (долгота)<input inputMode="decimal" value={value.east} onChange={set("east")} placeholder="37.70" required /></label>
    </div>
  );
}

export function AdminMapsPage() {
  const list = useLive(() => api.get<{ items: MapLayer[] }>("/api/v1/maps?all=true"), []);
  const [msg, setMsg] = useState<Msg>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [bounds, setBounds] = useState<BoundsForm>(EMPTY_BOUNDS);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editBounds, setEditBounds] = useState<BoundsForm>(EMPTY_BOUNDS);
  const [editName, setEditName] = useState("");

  const changed = () => {
    invalidateMaps();
    list.reload();
  };

  function pick(f: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(f);
    setPreview(f ? URL.createObjectURL(f) : "");
    if (f && !name) setName(f.name.replace(/\.[^.]+$/, ""));
  }

  async function uploadMap(e: FormEvent) {
    e.preventDefault();
    const bad = boundsValid(bounds);
    if (!file) return setMsg({ tone: "danger", text: "Выберите файл карты" });
    if (bad) return setMsg({ tone: "danger", text: bad });
    setBusy(true);
    const q = new URLSearchParams({ name, description, ...bounds });
    await run(setMsg, () => api.upload(`/api/v1/admin/maps?${q}`, file), "Карта загружена", () => {
      pick(null);
      setName("");
      setDescription("");
      setBounds(EMPTY_BOUNDS);
      changed();
    });
    setBusy(false);
  }

  async function saveEdit(m: MapLayer) {
    const bad = boundsValid(editBounds);
    if (bad) return setMsg({ tone: "danger", text: bad });
    const b = Object.fromEntries(Object.entries(editBounds).map(([k, v]) => [k, Number(v)]));
    await run(setMsg, () => api.patch(`/api/v1/admin/maps/${m.id}`, { name: editName, ...b }), "Карта обновлена", () => {
      setEditing(null);
      changed();
    });
  }

  const startEdit = (m: MapLayer) => {
    setEditing(m.id);
    setEditName(m.name);
    setEditBounds(Object.fromEntries(Object.entries(m.bounds).map(([k, v]) => [k, String(v)])) as BoundsForm);
  };

  return (
    <div className="stack">
      <h1>Карты-подложки</h1>
      <Alert tone={msg?.tone ?? "ok"}>{msg?.text}</Alert>
      <Card title="Загрузить карту">
        <p className="muted small">
          Изображение района работ (PNG, JPEG или WebP, до 50 МБ) — например, экспорт из ГИС или скан карты,
          ориентированный на север. Укажите географические координаты краёв изображения: широту верхнего (север)
          и нижнего (юг) края, долготу левого (запад) и правого (восток). Карта автоматически подкладывается под
          маршруты, точки которых попадают в её границы. Интернет не требуется.
        </p>
        <form className="stack" onSubmit={uploadMap}>
          <div className="form grid2">
            <label>Файл<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => pick(e.target.files?.[0] ?? null)} required /></label>
            <label>Название<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
            <label className="span2">Описание<input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} placeholder="Источник, дата, масштаб" /></label>
          </div>
          <BoundsInputs value={bounds} onChange={setBounds} />
          {preview && <div className="map-preview"><img src={preview} alt="Предпросмотр карты" /></div>}
          <div><button className="btn primary" disabled={busy}>{busy ? "Загрузка…" : "Загрузить"}</button></div>
        </form>
      </Card>
      <Card title="Загруженные карты">
        {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Карты не загружены — маршруты показываются схемой.</Empty> : (
          <table className="table">
            <thead><tr><th>Карта</th><th>Границы (С / Ю / З / В)</th><th>Размер</th><th>Статус</th><th /></tr></thead>
            <tbody>
              {list.data.items.map((m) => (
                <tr key={m.id}>
                  <td>
                    {editing === m.id ? <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                      : <><strong>{m.name}</strong>{m.description && <><br /><span className="muted small">{m.description}</span></>}</>}
                    <div className="map-preview"><img src={m.image_url} alt={m.name} loading="lazy" /></div>
                  </td>
                  <td>{editing === m.id ? <BoundsInputs value={editBounds} onChange={setEditBounds} />
                    : <span className="small">{m.bounds.north} / {m.bounds.south} / {m.bounds.west} / {m.bounds.east}</span>}</td>
                  <td className="small">{m.width_px && m.height_px ? `${m.width_px}×${m.height_px} px, ` : ""}{m.size_bytes < 1024 * 1024 ? `${Math.ceil(m.size_bytes / 1024)} КБ` : `${(m.size_bytes / 1024 / 1024).toFixed(1)} МБ`}</td>
                  <td><Tone tone={m.active ? "ok" : "warn"}>{m.active ? "используется" : "отключена"}</Tone></td>
                  <td className="row">
                    {editing === m.id ? (
                      <>
                        <button className="btn small primary" onClick={() => saveEdit(m)}>Сохранить</button>
                        <button className="btn small ghost" onClick={() => setEditing(null)}>Отмена</button>
                      </>
                    ) : (
                      <>
                        <button className="btn small" onClick={() => startEdit(m)}>Изменить</button>
                        <button className="btn small" onClick={() => run(setMsg, () => api.patch(`/api/v1/admin/maps/${m.id}`, { active: !m.active }),
                          m.active ? "Карта отключена" : "Карта включена", changed)}>{m.active ? "Отключить" : "Включить"}</button>
                        <button className="btn small danger" onClick={() => confirm(`Удалить карту «${m.name}»?`) &&
                          run(setMsg, () => api.del(`/api/v1/admin/maps/${m.id}`), "Карта удалена", changed)}>Удалить</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
