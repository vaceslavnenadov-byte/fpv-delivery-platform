import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ProfilePage } from "../pages/Profile";
import { OperatorMinePage, OperatorQueuePage, OperatorScoresPage } from "../pages/operator/OperatorPages";
import { supportRoutes } from "../pages/support/routes";
import { QualificationPage } from "../pages/licenses/QualificationPage";
import { mountApp } from "./common";

mountApp(APPS.operator, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route element={<ShellOutlet app={APPS.operator} />}>
      <Route path="/" element={<OperatorQueuePage />} />
      <Route path="/mine" element={<OperatorMinePage />} />
      <Route path="/scores" element={<OperatorScoresPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/qualification" element={<QualificationPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes()}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
