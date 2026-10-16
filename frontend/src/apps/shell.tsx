import { type ReactNode } from "react";
import { Outlet } from "react-router";
import type { AppConfig } from "./config";
import { roleHome } from "./config";
import { useAuth } from "../auth";
import { Layout } from "../components/Layout";
import { Pending } from "../components/ui";

/** Пока проверяется сессия — заглушка; без сессии — возврат на общий вход (/). */
export function Gate({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <Pending label="Проверка сессии…" />;
  if (!me) {
    window.location.replace("/");
    return null;
  }
  return <>{children}</>;
}

/** Клиент открыт не той ролью: сервер всё равно откажет, но подскажем, куда идти. */
function WrongApp({ app }: { app: AppConfig }) {
  const { can, logout } = useAuth();
  const home = roleHome(can);
  return (
    <div className="login-wrap">
      <div className="card login">
        <div className="brand big"><span className="brand-mark">◆</span> FPV Доставка</div>
        <p>Этот раздел — «{app.title.replace("FPV Доставка — ", "")}» — не доступен для вашей роли.</p>
        <div className="row">
          {home ? <a className="btn primary" href={home}>Перейти в свой раздел</a>
                : <a className="btn primary" href="/">Заполнить профиль</a>}
          <button className="btn ghost" onClick={async () => { await logout(); window.location.href = "/"; }}>Выйти</button>
        </div>
      </div>
    </div>
  );
}

/** Макет роли как layout-route: проверка роли, затем боковое меню и содержимое (Outlet). */
export function ShellOutlet({ app }: { app: AppConfig }) {
  const { can } = useAuth();
  if (!can(app.requiredPerm)) return <WrongApp app={app} />;
  return <Layout nav={app.nav}><Outlet /></Layout>;
}
