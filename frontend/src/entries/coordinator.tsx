import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ProfilePage } from "../pages/Profile";
import { CoordinatorPage } from "../pages/coordinator/CoordinatorPage";
import { mountApp } from "./common";

mountApp(APPS.coordinator, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route element={<ShellOutlet app={APPS.coordinator} />}>
      <Route path="/" element={<CoordinatorPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
