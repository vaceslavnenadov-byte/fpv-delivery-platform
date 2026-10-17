import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ProfilePage } from "../pages/Profile";
import { CoordinatorPage } from "../pages/coordinator/CoordinatorPage";
import { VideoWallPage } from "../pages/coordinator/VideoWall";
import { supportRoutes } from "../pages/support/routes";
import { mountApp } from "./common";

mountApp(APPS.coordinator, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route element={<ShellOutlet app={APPS.coordinator} />}>
      <Route path="/" element={<VideoWallPage />} />
      <Route path="/orders" element={<CoordinatorPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes()}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
