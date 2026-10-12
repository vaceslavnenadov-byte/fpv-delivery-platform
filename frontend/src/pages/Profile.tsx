import { useState, type FormEvent } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert, Card, KV, Tone } from "../components/ui";
import { ROLE_LABELS } from "../labels";

export function ProfilePage() {
  const { me, refresh, can } = useAuth();
  const [name, setName] = useState(me?.display_name ?? "");
  const [phone, setPhone] = useState(me?.phone ?? "");
  const [email, setEmail] = useState(me?.email ?? "");
  const [license, setLicense] = useState("");
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  if (!me) return null;

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api.put("/api/v1/me/profile", { display_name: name, phone: phone || null, email: email || null });
      await refresh();
      setMsg({ tone: "ok", text: "Профиль сохранён" });
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  async function apply(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/v1/me/operator-application", { license_number: license });
      await refresh();
      setMsg({ tone: "ok", text: "Заявка на допуск оператора отправлена администратору" });
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    }
  }

  return (
    <div className="stack">
      <h1>Профиль</h1>
      {!me.profile_completed && <Alert tone="info">Завершите регистрацию: проверьте имя и укажите контактный телефон.</Alert>}
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      <div className="grid2">
        <Card title="Учётная запись">
          <KV items={[
            ["Доменное имя", me.username],
            ["Роли приложения", me.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")],
            ["Доменные группы", me.groups.join(", ") || "—"],
            ["Допуск оператора", me.operator_status ? <Tone tone={me.operator_status === "approved" ? "ok" : "warn"}>{me.operator_status}</Tone> : "—"],
          ]} />
          <p className="muted small">Роли назначаются через членство в доменных группах; права проверяются сервером при каждом запросе.</p>
        </Card>
        <Card title="Контактные данные">
          <form className="form" onSubmit={save}>
            <label>Отображаемое имя<input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></label>
            <label>Телефон<input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+7 900 000-00-00" /></label>
            <label>Электронная почта<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <button className="btn primary">Сохранить</button>
          </form>
        </Card>
      </div>
      {can("operator.apply") && me.roles.includes("operator") && me.operator_status !== "approved" && (
        <Card title="Допуск оператора">
          {me.operator_status === "pending" ? <p>Заявка на рассмотрении администратора.</p> : (
            <form className="form row" onSubmit={apply}>
              <label className="grow">Номер свидетельства/удостоверения оператора
                <input value={license} onChange={(e) => setLicense(e.target.value)} required minLength={3} />
              </label>
              <button className="btn primary">Подать заявку</button>
            </form>
          )}
        </Card>
      )}
    </div>
  );
}
