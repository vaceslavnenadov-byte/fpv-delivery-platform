import React, { useEffect } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
import { roleHome } from "../apps/config";
import { AuthProvider, useAuth } from "../auth";
import { Pending } from "../components/ui";
import { LoginPage } from "../pages/Login";
import { ProfilePage } from "../pages/Profile";
import "../styles.css";

// Общий вход (адрес /). После входа пользователь переходит в клиент своей роли;
// пользователь без назначенной роли остаётся здесь и заполняет профиль.
function Landing() {
  const { me, loading, logout } = useAuth();
  const home = me ? roleHome((p) => me.permissions.includes(p)) : null;

  useEffect(() => {
    if (home) window.location.replace(home);
  }, [home]);

  if (loading) return <Pending label="Проверка сессии…" />;
  if (!me) return <LoginPage />;
  if (home) return <Pending label="Открываем ваш раздел…" />;

  // Вошёл, но роль ещё не назначена администратором.
  return (
    <div className="main">
      <header className="topbar">
        <div className="muted small">FPV Доставка</div>
        <div className="topbar-right">
          <div className="who"><strong>{me.display_name}</strong><span className="muted small">{me.username}</span></div>
          <button className="btn ghost" onClick={async () => { await logout(); window.location.reload(); }}>Выйти</button>
        </div>
      </header>
      <main className="content">
        <ProfilePage />
      </main>
    </div>
  );
}

document.title = "FPV Доставка — вход";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Landing />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
