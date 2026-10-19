import { useEffect, type ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router";
import type { AppConfig } from "./config";
import { roleHome, suspendedApp } from "./config";
import { useAuth } from "../auth";
import { Layout } from "../components/Layout";
import { Pending } from "../components/ui";

/** Пока проверяется сессия — заглушка; без сессии — общий вход (/) с адресом возврата,
 *  чтобы после входа вернуться туда же (например, в окно монитора полёта). */
export function Gate({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const noSession = !loading && !me;
  useEffect(() => {
    if (noSession) {
      const back = window.location.pathname + window.location.search;
      window.location.replace(`/?next=${encodeURIComponent(back)}`);
    }
  }, [noSession]);
  if (loading || !me) return <Pending label="Проверка сессии…" />;
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
  const { can, me } = useAuth();
  const { pathname } = useLocation();
  const unverified = me?.profile_verified === false;
  if (me && (me.account_status === "suspended" || unverified)) {
    // Приостановлен или профиль ещё не подтверждён: в разделе своей роли — только профиль и техподдержка.
    if (suspendedApp(me.roles).key !== app.key) return <WrongApp app={app} />;
    const allowed = pathname.startsWith("/support") || pathname.startsWith("/profile");
    return <Layout app={app}>{allowed ? <Outlet /> : <SuspendedNotice unverified={unverified && me.account_status !== "suspended"} />}</Layout>;
  }
  if (!can(app.requiredPerm)) return <WrongApp app={app} />;
  return <Layout app={app}><Outlet /></Layout>;
}

function SuspendedNotice({ unverified = false }: { unverified?: boolean }) {
  return (
    <div className="card stack">
      <h1>{unverified ? "Учётная запись не подтверждена" : "Учётная запись приостановлена"}</h1>
      <p>{unverified
        ? "Профиль проверяют администратор или рекрутер. После подтверждения откроются рабочие разделы. Пока доступны профиль и техподдержка."
        : "Должностные разделы временно недоступны. Вы можете написать в техподдержку или посмотреть свои обращения."}</p>
      <div className="row">
        <Link className="btn primary" to="/support/new">Новое обращение</Link>
        <Link className="btn" to="/support">Мои обращения</Link>
        <Link className="btn ghost" to="/profile">Профиль</Link>
      </div>
    </div>
  );
}
