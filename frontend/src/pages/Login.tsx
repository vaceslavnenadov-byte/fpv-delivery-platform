import { ThemeSwitch } from "../theme";
import { useState, type FormEvent } from "react";
import { errorText } from "../api";
import { useAuth } from "../auth";
import { Alert } from "../components/ui";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [device, setDevice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password, device || undefined);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <div className="row between"><div className="brand big"><span className="brand-mark">◆</span> FPV Доставка</div><ThemeSwitch /></div>
        <p className="muted">Гражданская доставка с использованием дронов. Вход — доменная учётная запись организации.</p>
        <label>Имя пользователя домена
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
                 placeholder="ivanov или FPV\ivanov" required />
        </label>
        <label>Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="current-password" required />
        </label>
        <details>
          <summary className="small">Рабочее место (если требуется политикой доступа)</summary>
          <label>Код устройства
            <input value={device} onChange={(e) => setDevice(e.target.value)} placeholder="WS-OPS-01" />
          </label>
        </details>
        <Alert>{error}</Alert>
        <button className="btn primary" disabled={busy}>{busy ? "Проверка…" : "Войти"}</button>
        <p className="muted small">
          Пароль проверяется службой каталогов и не сохраняется приложением.
          Регистрация учётной записи выполняется сетевым администратором в домене;
          после первого входа заполните профиль.
        </p>
      </form>
    </div>
  );
}
