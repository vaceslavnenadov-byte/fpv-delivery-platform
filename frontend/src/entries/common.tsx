import React, { type ReactElement } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
import type { AppConfig } from "../apps/config";
import { Gate } from "../apps/shell";
import { AuthProvider } from "../auth";
import { setAppBase } from "../base";
import "../styles.css";

/** Монтирует клиент роли: общий вход при отсутствии сессии, затем маршруты роли. */
export function mountApp(app: AppConfig, routes: ReactElement): void {
  document.title = app.title;
  setAppBase(app.base);
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <BrowserRouter basename={app.base}>
        <AuthProvider>
          <Gate>{routes}</Gate>
        </AuthProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
}
