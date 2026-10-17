import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ServiceAreaPage } from "../pages/admin/ServiceAreaPage";
import { ProfilePage } from "../pages/Profile";
import { AdminAuditPage, AdminComplaintsPage, AdminDronesPage, AdminMapsPage, AdminMonitoringPage,
  AdminOrdersPage, AdminOverviewPage, AdminReviewsPage, AdminScoringPage, AdminUsersPage } from "../pages/admin/AdminPages";
import { mountApp } from "./common";

mountApp(APPS.admin, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route element={<ShellOutlet app={APPS.admin} />}>
      <Route path="/" element={<AdminOverviewPage />} />
      <Route path="/reviews" element={<AdminReviewsPage />} />
      <Route path="/orders" element={<AdminOrdersPage />} />
      <Route path="/drones" element={<AdminDronesPage />} />
      <Route path="/complaints" element={<AdminComplaintsPage />} />
      <Route path="/users" element={<AdminUsersPage />} />
      <Route path="/scoring" element={<AdminScoringPage />} />
      <Route path="/maps" element={<AdminMapsPage />} />
      <Route path="/service-area" element={<ServiceAreaPage />} />
      <Route path="/audit" element={<AdminAuditPage />} />
      <Route path="/monitoring" element={<AdminMonitoringPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
